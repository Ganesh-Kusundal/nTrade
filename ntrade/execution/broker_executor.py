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

from ntrade.domain.orders.order import OrderStatus, OrderType, TradeType
from ntrade.events.order import (
    OrderAcceptedEvent,
    OrderFilledEvent,
    OrderIntentEvent,
    OrderRejectedEvent,
    OrderTimeoutEvent,
    OrderUpdatedEvent,
)

logger = logging.getLogger("ntrade.execution")


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

    def __init__(self, context, broker):
        self.ctx = context
        self.broker = broker
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
            order = instrument.order.place(
                intent.side, intent.quantity,
                order_type=OrderType(intent.order_type.upper()),
                trade_type=TradeType.MIS, price=intent.price,
            )
        except Exception as exc:  # broker rejection surfaces as an exception
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity, reason=str(exc),
                strategy=intent.strategy, ts=intent.ts,
            )
        self._seq += 1
        order_id = order.order_id or f"BRK-{self._seq:06d}"
        if order_id == "None":  # some brokers turn a missing id into the string "None"
            order_id = f"BRK-{self._seq:06d}"
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
        fill = OrderFilledEvent(
            order_id=order_id, symbol=intent.symbol, exchange=intent.exchange,
            side=intent.side, quantity=new_qty, fill_price=price,
            strategy=intent.strategy, ts=self.ctx.now(),
        )
        self.ctx.bus.publish(fill)
        logger.info("filled %s %s x%d @ %.2f (order %s)",
                    intent.side, intent.symbol, new_qty, price, order_id)
        if emitted is not None:
            emitted.append(fill)
        if record:
            record["filled"] += new_qty
