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

from ntrade.domain.orders.order import Order, OrderSide, OrderStatus, OrderType
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

logger = logging.getLogger("ntrade.execution")

_TERMINAL_STATUSES = ("COMPLETED", "REJECTED", "CANCELLED")


def _fill_price(order) -> float:
    try:
        return float(order.avg_price or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fill_qty(order) -> int:
    try:
        return int(order.filled_qty or 0)
    except (TypeError, ValueError):
        return 0


class BrokerExecution:
    name = "broker"

    def __init__(self, context, broker, *,
                 commission: CommissionModel | None = None,
                 statutory=STATUTORY_DEFAULT):
        self.ctx = context
        self.broker = broker
        self.commission = commission or FlatCommission(0.0)
        # None → zero-cost opt-out; STATUTORY_DEFAULT → IndianStatutoryCosts().
        self.statutory: IndianStatutoryCosts | None = resolve_statutory(statutory)
        self._seq = 0
        # order_id -> {"intent": ..., "order": ..., "filled": int}
        self._open: dict[str, dict] = {}
        self._stale_limit = 10  # max consecutive status-refresh failures before eviction

    # ------------------------------------------------------------- submission
    def submit(self, intent: OrderIntentEvent) -> OrderRejectedEvent | None:
        """Place the order; publish accepted immediately, fill if synchronous."""
        instrument = self.ctx.instrument(intent.symbol)
        if instrument is None or instrument.broker_adapter is None:
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity, reason="no broker-backed instrument",
                strategy=intent.strategy, ts=intent.ts,
            )
        try:
            # trade_type omitted: OrderFacade.place applies the K-024 default
            # (CNC delivery for equity/ETF, MIS for derivatives) — forcing MIS
            # here turned every kernel-routed equity order intraday.
            order = instrument.order.place(
                intent.side, intent.quantity,
                order_type=OrderType(intent.order_type.upper()),
                price=intent.price,
            )
        except Exception as exc:  # broker rejection surfaces as an exception
            # Ambiguous failure: the exchange may have accepted the order
            # before the transport died. The next reconcile_open() adopts any
            # orphaned broker-side order, so nothing is silently lost.
            logger.warning(
                "order placement failed for %s %s x%d: %s — if the exchange "
                "accepted it, reconcile_open() will adopt the orphan",
                intent.side, intent.symbol, intent.quantity, exc,
            )
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity, reason=str(exc),
                strategy=intent.strategy, ts=intent.ts,
            )
        order_id = str(order.order_id) if order.order_id else ""
        if not order_id or order_id == "None":
            # Some brokers turn a missing id into the string "None"; allocate a
            # fresh BRK- id that never collides with an existing open order, so
            # two orders can never merge under one key (M2).
            order_id = self._next_brk_id()
        order.order_id = order_id  # the tracked order must carry its key for poll()
        self.ctx.bus.publish(OrderAcceptedEvent(
            order_id=order_id, symbol=intent.symbol, exchange=intent.exchange,
            side=intent.side, quantity=intent.quantity, strategy=intent.strategy, ts=intent.ts,
        ))
        if order.is_filled:  # synchronous broker (PaperBroker / instant fill)
            self._emit_fill(order_id, intent, order)
        else:
            self._open[order_id] = {"intent": intent, "order": order, "filled": 0,
                                    "status": order.status, "placed_at": self.ctx.now()}
        return None

    def _next_brk_id(self) -> str:
        """A unique ``BRK-`` fallback id (never collides with open orders)."""
        while True:
            self._seq += 1
            candidate = f"BRK-{self._seq:06d}"
            if candidate not in self._open:
                return candidate

    # --------------------------------------------------------- lifecycle poll
    def poll(self) -> list:
        """Refresh open orders and publish fills/rejections as the broker reports.

        Returns the newly published OrderFilledEvent / OrderRejectedEvent list.
        Idempotent: an order already terminal emits nothing on later polls; a
        partial fill is surfaced as its filled quantity and never re-emitted.
        OrderUpdatedEvent is published whenever an order's status changes.
        """
        emitted = []
        for order_id in list(self._open):
            record = self._open[order_id]
            order = record["order"]
            try:
                self.broker.get_order_status(order)
                record["stale"] = 0  # reset on success
            except RateLimited:
                # Quota backoff is NOT staleness (H-5): leave the record
                # untouched and retry on the next poll — penalizing DH-904
                # with eviction dropped tracked orders under rate pressure.
                continue
            except Exception:
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
            if placed_at is not None and order.status == OrderStatus.PENDING:
                age = (self.ctx.now() - placed_at).total_seconds()
                if age > 300:  # 5-minute timeout
                    logger.warning("order %s timed out after %.0fs", order_id, age)
                    self.ctx.bus.publish(OrderTimeoutEvent(
                        order_id=order_id, symbol=record["intent"].symbol,
                        exchange=record["intent"].exchange, side=record["intent"].side,
                        quantity=record["intent"].quantity, age_seconds=age,
                        strategy=record["intent"].strategy, ts=self.ctx.now(),
                    ))
            if order.status != record["status"]:
                record["status"] = order.status
                self.ctx.bus.publish(OrderUpdatedEvent(
                    order_id=order_id, symbol=record["intent"].symbol,
                    exchange=record["intent"].exchange, side=record["intent"].side,
                    status=order.status.value, filled_qty=_fill_qty(order),
                    avg_price=_fill_price(order), strategy=record["intent"].strategy,
                    ts=self.ctx.now(),
                ))
            self._emit_fill(order_id, record["intent"], order, emitted)
            status = order.status
            if status == OrderStatus.COMPLETED:
                del self._open[order_id]
            elif status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
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
                del self._open[order_id]
        return emitted

    def open_orders(self) -> list[str]:
        """Order ids still open (accepted, awaiting broker lifecycle)."""
        return list(self._open)

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
        for entry in book:
            order_id = str(entry.order_id or "")
            if not order_id or order_id in self._open:
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
                status = OrderStatus(entry.status) if entry.status else OrderStatus.PENDING
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
        record = self._open.get(order_id)
        if record is None:
            return None
        return self.broker.modify_order(record["order"], price=price,
                                        quantity=quantity, order_type=order_type,
                                        trigger_price=trigger_price)

    def cancel(self, order_id: str):
        """Cancel an open order through the broker."""
        record = self._open.get(order_id)
        if record is None:
            return None
        return self.broker.cancel_order(record["order"])

    # ------------------------------------------------------------------ fills
    def _emit_fill(self, order_id: str, intent: OrderIntentEvent,
                   order, emitted: list | None = None) -> None:
        """Emit the newly-filled quantity since the last poll (partial-safe)."""
        record = self._open.get(order_id)
        already = record["filled"] if record else 0
        new_qty = _fill_qty(order) - already
        if new_qty <= 0:
            return
        price = _fill_price(order) or intent.price or 0.0
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
