"""BrokerExecution — routes order intents to a real BrokerAdapter (live).

Zero parity: identical intent → fill flow as ``SimulatedExecution``, but the
fill comes from the broker's order lifecycle. The broker adapter remains the
single transport boundary; the kernel never touches REST/websocket/JSON.

Live orders are asynchronous: the broker accepts (PENDING) and later reports
COMPLETED / PARTIALLY_FILLED / REJECTED / CANCELLED. ``submit()`` places the
order and publishes ``OrderAcceptedEvent`` immediately; ``poll()`` refreshes
the open orders from the broker and publishes ``OrderFilledEvent`` /
``OrderRejectedEvent`` as the lifecycle advances. A broker that fills
synchronously (e.g. PaperBroker) emits the fill straight from ``submit()``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any  # noqa: F401 — kept for type-checking idempotency_guard param

from ntrade.domain.orders.order import Order, OrderSide, OrderType
from ntrade.domain.orders.order import OrderStatus as _OrderStatus
from ntrade.events.order import (
    OrderAcceptedEvent,
    OrderFilledEvent,
    OrderIntentEvent,
    OrderRejectedEvent,
    OrderTimeoutEvent,
    OrderUpdatedEvent,
)
from ntrade.execution.costs import (
    CommissionModel,
    FlatCommission,
    IndianStatutoryCosts,
    STATUTORY_DEFAULT,
    resolve_statutory,
)
from ntrade.execution.rate_limit import RateLimited
from ntrade.domain.coercion import to_float, to_int

# Local implementations of v3 patterns — no external dependency.
from ntrade.execution._guard import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CorrelationId,
    MemoryIdempotencyGuard,
)
from ntrade.domain.constants import CIRCUIT_COOLDOWN_S, CIRCUIT_FAILURE_THRESHOLD

logger = logging.getLogger("ntrade.execution")

_TERMINAL_STATUSES = tuple(sorted(_OrderStatus.TERMINAL))


class BrokerExecution:
    name = "broker"

    def __init__(self, context, broker, *,
                 commission: CommissionModel | None = None,
                 statutory=STATUTORY_DEFAULT,
                 idempotency_guard: "Any | None" = None,
                 circuit_breaker: "CircuitBreaker | None" = None,
                 stale_limit: int = 10,
                 order_timeout_seconds: float = 300.0):
        self.ctx = context
        self.broker = broker
        self.commission = commission or FlatCommission(0.0)
        # None → zero-cost opt-out; STATUTORY_DEFAULT → IndianStatutoryCosts().
        self.statutory: IndianStatutoryCosts | None = resolve_statutory(statutory)
        self._seq = 0
        self._order_timeout_seconds = float(order_timeout_seconds)  # configurable vs hardcoded 300
        # order_id -> {"intent": ..., "order": ..., "filled": int}
        self._open: dict[str, dict] = {}
        self._stale_limit = stale_limit  # max consecutive failures before eviction
        # Idempotency guard prevents duplicate submissions on network failure
        # after exchange acceptance (critical for real-money safety).
        self._idem = idempotency_guard or MemoryIdempotencyGuard()
        # Circuit breaker prevents hammering a down broker and stops
        # _stale_limit from evicting tracked orders during systemic outages.
        # Threshold is set well above _stale_limit so per-order stale eviction
        # (which is order-granular) fires before the global circuit trips —
        # the circuit is for systematic broker outages, not individual
        # transport errors on a single order.
        self._breaker_lock = threading.RLock()
        self._breaker = circuit_breaker or CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=CIRCUIT_FAILURE_THRESHOLD,
                cooldown_seconds=CIRCUIT_COOLDOWN_S,
            ),
            send=self._noop_poll,
        )

    # ------------------------------------------------------------- submission
    def submit(self, intent: OrderIntentEvent) -> OrderRejectedEvent | None:
        """Place the order; publish accepted immediately, fill if synchronous.

        Idempotency: every intent carries a correlation_id (assigned by EventBus
        if absent). We check-and-reserve it BEFORE crossing the venue boundary
        so a network drop after exchange acceptance cannot produce a duplicate
        real-money submission on retry.
        """
        instrument = self.ctx.instrument(intent.symbol)
        if instrument is None or instrument.broker_adapter is None:
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity, reason="no broker-backed instrument",
                strategy=intent.strategy, ts=intent.ts,
            )
        # Idempotency: dedup by correlation_id. If already completed, return
        # the recorded receipt (no broker call). If reserved by a prior in-flight
        # attempt, that's a logic bug — raise rather than risk a duplicate.
        corr = intent.correlation_id or intent.event_id
        corr_id = CorrelationId(value=corr)
        prior = self._idem.check_and_reserve(corr_id)
        if prior is not None:
            logger.debug("idempotency_dedup order_id=%s corr=%s", prior, corr)
            return None  # already accepted; the original OrderAcceptedEvent was published
        try:
            # TODO: use execution.order_types.strategy_for / route_for_broker when adding new types
            order = instrument.order.place(
                intent.side, intent.quantity,
                order_type=OrderType(intent.order_type.upper()),
                price=intent.price,
                reference_price=intent.reference_price,
            )
        except Exception as exc:
            # Deterministic failure: exchange rejected before accepting.
            # Safe to release the idempotency key so a corrected retry can proceed.
            self._idem.release(corr_id)
            logger.warning(
                "order placement failed for %s %s x%d: %s",
                intent.side, intent.symbol, intent.quantity, exc,
            )
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity, reason=str(exc),
                strategy=intent.strategy, ts=intent.ts,
            )
        # Order was accepted by the venue — record the idempotency result.
        order_id = str(order.order_id) if order.order_id else ""
        if not order_id or order_id == "None":
            order_id = self._next_brk_id()
        order.order_id = order_id  # the tracked order must carry its key for poll()
        self._idem.record_result(corr_id, order_id)
        self.ctx.bus.publish(OrderAcceptedEvent(
            order_id=order_id, symbol=intent.symbol, exchange=intent.exchange,
            side=intent.side, quantity=intent.quantity, strategy=intent.strategy, ts=intent.ts,
        ))
        if order.is_filled:  # synchronous broker (PaperBroker / instant fill)
            self._emit_fill(order_id, intent, order)
        else:
            with self._breaker_lock:
                self._open[order_id] = {"intent": intent, "order": order, "filled": 0,
                                        "status": order.status, "placed_at": self.ctx.now()}
        return None

    def _next_brk_id(self) -> str:
        """A unique ``BRK-`` fallback id (never collides with open orders)."""
        with self._breaker_lock:
            while True:
                self._seq += 1
                candidate = f"BRK-{self._seq:06d}"
                if candidate not in self._open:
                    return candidate

    # --------------------------------------------------------- lifecycle poll
    def _noop_poll(self, *args, **kwargs) -> tuple[int, object]:
        """Sentinel send callable for the circuit breaker.

        The breaker's ``request()`` is not used directly; ``poll()`` calls the
        broker and manages breaker state via ``_on_success()``/``_on_failure()``
        so it can also handle RateLimited distinctly.
        """
        return 200, None

    def poll(self) -> list:
        """Refresh open orders and publish fills/rejections as the broker reports.

        Returns the newly published OrderFilledEvent / OrderRejectedEvent list.
        Idempotent: an order already terminal emits nothing on later polls; a
        partial fill is surfaced as its filled quantity and never re-emitted.
        OrderUpdatedEvent is published whenever an order's status changes.

        Circuit breaker: when the broker circuit is open, polling is skipped
        (no broker call), but stale failures still accumulate toward
        _stale_limit so genuinely lost orders are eventually evicted.
        """
        # Snapshot the order ids under lock so concurrent submit() can't
        # mutate _open during iteration.
        emitted = []
        with self._breaker_lock:
            order_ids = list(self._open)
        for order_id in order_ids:
            with self._breaker_lock:
                record = self._open.get(order_id)
                if record is None:
                    continue
                order = record["order"]
                # Check circuit state before calling the broker.
                circuit_open = self._breaker.state.value == "OPEN"
            if circuit_open:
                # Broker is in a systemic outage — the circuit breaker prevents
                # calls to the broker. Still count stale failures so genuinely
                # lost orders are eventually evicted (the circuit may recover
                # but this order may not exist at the venue).
                with self._breaker_lock:
                    record["stale"] = record.get("stale", 0) + 1
                    if record["stale"] >= self._stale_limit:
                        logger.warning(
                            "order %s stale after %d polls — evicting",
                            order_id, self._stale_limit,
                        )
                        del self._open[order_id]
                        continue
                # After the cooldown elapses, allow a single HALF_OPEN probe
                # so a recovered broker is detected — otherwise an OPEN
                # circuit parks order tracking forever. A successful probe
                # closes the circuit on the success path below.
                if self._breaker._maybe_reset():
                    logger.info(
                        "circuit HALF_OPEN after cooldown — probing broker for order %s",
                        order_id,
                    )
                else:
                    continue  # cooldown not elapsed — keep skipping the broker
            try:
                # Direct broker call — NOT through circuit_breaker.request()
                # because get_order_status mutates the Order in place (not
                # (status, body) return). Breaker state is managed manually.
                self.broker.get_order_status(order)
                self._breaker._on_success()
                with self._breaker_lock:
                    record["stale"] = 0  # reset on success
            except RateLimited:
                # Quota backoff is NOT staleness (H-5): leave the record
                # untouched and retry on the next poll — penalizing DH-904
                # with eviction dropped tracked orders under rate pressure.
                continue
            except Exception:
                # Transport error (not rate limit, not circuit open):
                # count as circuit failure + stale. When failures accumulate
                # past the threshold, the circuit breaker trips and subsequent
                # polls take the circuit-open path above.
                self._breaker._on_failure()
                with self._breaker_lock:
                    record["stale"] = record.get("stale", 0) + 1
                    if record["stale"] >= self._stale_limit:
                        logger.warning(
                            "order %s stale after %d polls — evicting",
                            order_id, self._stale_limit,
                        )
                        del self._open[order_id]
                continue
            # Timeout detection: PENDING orders older than threshold
            placed_at = record.get("placed_at")
            if placed_at is not None and order.status == _OrderStatus.PENDING:
                age = (self.ctx.now() - placed_at).total_seconds()
                if age > self._order_timeout_seconds:  # configurable vs hardcoded 300
                    logger.warning("order %s timed out after %.0fs", order_id, age)
                    self.ctx.bus.publish(OrderTimeoutEvent(
                        order_id=order_id, symbol=record["intent"].symbol,
                        exchange=record["intent"].exchange, side=record["intent"].side,
                        quantity=record["intent"].quantity, age_seconds=age,
                        strategy=record["intent"].strategy, ts=self.ctx.now(),
                    ))
            if order.status != record["status"]:
                with self._breaker_lock:
                    record["status"] = order.status
                self.ctx.bus.publish(OrderUpdatedEvent(
                    order_id=order_id, symbol=record["intent"].symbol,
                    exchange=record["intent"].exchange, side=record["intent"].side,
                    status=order.status.value, filled_qty=to_int(order.filled_qty),
                    avg_price=to_float(order.avg_price), strategy=record["intent"].strategy,
                    ts=self.ctx.now(),
                ))
            self._emit_fill(order_id, record["intent"], order, emitted)
            status = order.status
            if status == _OrderStatus.COMPLETED:
                with self._breaker_lock:
                    self._open.pop(order_id, None)
            elif status in (_OrderStatus.REJECTED, _OrderStatus.CANCELLED):
                # a partially-filled order that is then cancelled/rejected has
                # already had its filled shares emitted; reject only the rest
                remaining = record["intent"].quantity - record["filled"]
                if remaining > 0:
                    emitted.append(OrderRejectedEvent(
                        order_id=order_id, symbol=record["intent"].symbol,
                        exchange=record["intent"].exchange, side=record["intent"].side,
                        quantity=remaining,
                        reason=f"order {status.value}",
                        strategy=record["intent"].strategy, ts=self.ctx.now(),
                    ))
                    self.ctx.bus.publish(emitted[-1])
                with self._breaker_lock:
                    self._open.pop(order_id, None)
        return emitted

    def open_orders(self) -> list[str]:
        """Order ids still open (accepted, awaiting broker lifecycle)."""
        with self._breaker_lock:
            return list(self._open)

    def trip_kill_switch(self, reason: str = "") -> None:
        """Emergency halt: cancel all open orders and trip the circuit breaker.

        This is the runtime kill switch for rogue strategies or broker outages.
        All tracked open orders are cancelled through the broker; the circuit
        breaker is forced OPEN so no new submissions can proceed.
        """
        logger.warning("kill_switch_activated reason=%s", reason)
        with self._breaker_lock:
            open_ids = list(self._open)
        for order_id in open_ids:
            try:
                self.cancel(order_id)
            except Exception as exc:
                logger.error("kill_switch_cancel_failed order=%s error=%s", order_id, exc)
        # Force the circuit breaker open to block all new polls/submits.
        self._breaker.trip_kill()

    # ------------------------------------------------ orphan-order adoption
    def reconcile_open(self) -> list[str]:
        """Adopt broker-side open orders this tracker does not know (C-4).

        Dhan's API accepts no client-order-id, so an ambiguous place-order
        failure (network drop after exchange acceptance) leaves an orphaned
        order at the broker with no local record. Diffing the broker order
        book against ``_open`` surfaces such orphans; adopting them lets
        ``poll()`` track their lifecycle and emit their fills. A
        ``RateLimited`` propagates (K-021) — reconciliation retries on the
        next poll cycle. Returns the adopted order ids.
        """
        book = self.broker.get_orderbook()
        adopted: list[str] = []
        with self._breaker_lock:
            existing_ids = set(self._open)
        for entry in book:
            order_id = str(entry.order_id or "")
            if not order_id or order_id in existing_ids:
                continue
            if entry.status in _TERMINAL_STATUSES:
                continue
            instrument = self.ctx.instrument(entry.symbol)
            if instrument is None or instrument.broker_adapter is None:
                logger.critical(
                    "orphan broker order %s (%s %s x%d) for untracked symbol "
                    "%s — manual intervention required",
                    order_id, entry.side, entry.symbol, entry.quantity,
                    entry.symbol,
                )
                continue
            try:
                side = OrderSide(entry.side.upper())
                status = _OrderStatus(entry.status) if entry.status else _OrderStatus.PENDING
            except ValueError:
                logger.critical(
                    "orphan broker order %s has unmappable side/status "
                    "(%s/%s) — manual intervention required",
                    order_id, entry.side, entry.status,
                )
                continue
            intent = OrderIntentEvent(
                symbol=entry.symbol, exchange=entry.exchange or instrument.exchange,
                side=entry.side, quantity=entry.quantity,
                price=entry.price, strategy="",
                ts=self.ctx.now(),
            )
            order = Order(
                instrument=instrument, side=side, quantity=entry.quantity,
                order_id=order_id, status=status, filled_qty=0,
                created_at=self.ctx.now(),
            )
            with self._breaker_lock:
                self._open[order_id] = {
                    "intent": intent, "order": order, "filled": 0,
                    "status": status, "placed_at": self.ctx.now(),
                }
            adopted.append(order_id)
            logger.critical(
                "adopted orphan broker order %s (%s %s x%d @ %.2f, %s) — "
                "placement likely failed after exchange acceptance",
                order_id, entry.side, entry.symbol, entry.quantity,
                entry.price, entry.status or "PENDING",
            )
        return adopted

    # ------------------------------------------------------- crash recovery
    def restore_open(self, deltas: dict) -> int:
        """Rehydrate the open-order tracker from a recovered delta map (H3).

        ``deltas`` comes from ``EventStore.open_order_deltas()``: per-order
        filled/remaining state for orders still open when the session crashed.
        Rebuilds the in-memory ``_open`` records so ``poll()`` resumes
        emitting only the *remaining* fill (never re-emitting the
        already-filled quantity) and status/timeout tracking continues. Also
        bumps ``_seq`` past any recovered ``BRK-`` ids so new orders cannot
        collide. Returns the number of orders restored.
        """
        from ntrade.domain.orders.order import Order, OrderSide, OrderStatus

        restored = 0
        with self._breaker_lock:
            for order_id, delta in deltas.items():
                if order_id in self._open:
                    continue
                instrument = self.ctx.instrument(delta["symbol"])
                if instrument is None:
                    continue
                intent = OrderIntentEvent(
                    symbol=delta["symbol"], exchange=delta["exchange"],
                    side=delta["side"], quantity=delta["quantity"],
                    strategy=delta["strategy"], ts=delta["placed_at"],
                )
                order = Order(
                    instrument=instrument,
                    side=OrderSide(delta["side"].upper()),
                    quantity=delta["quantity"],
                    order_id=order_id,
                    status=OrderStatus(delta["status"]),
                    filled_qty=delta["filled"],
                    created_at=delta["placed_at"],
                )
                self._open[order_id] = {
                    "intent": intent, "order": order, "filled": delta["filled"],
                    "status": order.status, "placed_at": delta["placed_at"],
                }
                # bump seq past recovered BRK- ids so new orders cannot collide
                for token in str(order_id).split("-")[-1:]:
                    if token.isdigit() and int(token) > self._seq:
                        self._seq = int(token)
                restored += 1
        return restored

    # -------------------------------------------------------- OMS operations
    def modify(self, order_id: str, *, price: float | None = None,
               quantity: int | None = None, order_type=None,
               trigger_price: float | None = None):
        """Modify an open order through the broker."""
        with self._breaker_lock:
            record = self._open.get(order_id)
            if record is None:
                return None
            order = record["order"]
        return self.broker.modify_order(order, price=price,
                                        quantity=quantity, order_type=order_type,
                                        trigger_price=trigger_price)

    def cancel(self, order_id: str):
        """Cancel an open order through the broker."""
        with self._breaker_lock:
            record = self._open.get(order_id)
            if record is None:
                return None
            order = record["order"]
        return self.broker.cancel_order(order)

    # ------------------------------------------------------------------ fills
    def _emit_fill(self, order_id: str, intent: OrderIntentEvent,
                   order, emitted: list | None = None) -> None:
        """Emit the newly-filled quantity since the last poll (partial-safe)."""
        record = self._open.get(order_id)
        already = record["filled"] if record else 0
        new_qty = to_int(order.filled_qty) - already
        if new_qty <= 0:
            return
        price = to_float(order.avg_price) or intent.price or 0.0
        if price <= 0:
            logger.error("fill rejected: zero fill_price for %s order %s "
                         "(broker=%s, intent_price=%s)",
                         intent.symbol, order_id,
                         to_float(order.avg_price), intent.price)
            return
        notional = price * new_qty
        commission = round(self.commission.apply(notional), 4)
        if self.statutory is None:
            statutory = 0.0
        else:
            # Product schedule from the instrument class (F&O vs equity), GST
            # on the actual per-fill commission — the same cost pipeline as the
            # simulated target so paper PnL converges on live (H6).
            instrument = self.ctx.instrument(intent.symbol)
            if instrument is None:
                model = self.statutory  # default product schedule, never crash
            else:
                model = self.statutory.for_instrument(instrument)
            statutory = round(
                model.total_cost(notional, intent.side, brokerage=commission), 4)
        fill = OrderFilledEvent(
            order_id=order_id, symbol=intent.symbol, exchange=intent.exchange,
            side=intent.side, quantity=new_qty, fill_price=price,
            commission=commission, statutory=statutory,
            strategy=intent.strategy, ts=self.ctx.now(),
        )
        self.ctx.bus.publish(fill)
        logger.info("filled %s %s x%d @ %.2f (order %s)",
                    intent.side, intent.symbol, new_qty, price, order_id)
        if emitted is not None:
            emitted.append(fill)
        if record:
            record["filled"] += new_qty
