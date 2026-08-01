"""OMS order-state events + modify/cancel (G2-D1)."""
import types

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderUpdatedEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _broker_with_status_flow():
    """Stub Tradehull: PENDING on place; PARTIAL then COMPLETE on successive polls."""
    state = {"stage": "pending", "cancelled": False, "cancelled_calls": 0}

    def order_placement(**kw):
        return "ORD-1"

    def get_order_status(orderid=None, **kw):
        if state["cancelled"]:
            return "CANCELLED"
        if state["stage"] == "pending":
            state["stage"] = "partial"
            return "PARTIAL"
        state["stage"] = "complete"
        return "COMPLETE"

    def get_order_detail(orderid=None, **kw):
        if state["cancelled"]:
            return {"orderId": orderid, "orderStatus": "CANCELLED",
                    "filledQty": 2, "avgPrice": 100.0}
        if state["stage"] == "partial":
            return {"orderId": orderid, "orderStatus": "PARTIAL",
                    "filledQty": 2, "avgPrice": 100.0}
        return {"orderId": orderid, "orderStatus": "COMPLETE",
                "filledQty": 5, "avgPrice": 100.0}

    def cancel_order(**kw):
        state["cancelled"] = True
        state["cancelled_calls"] += 1
        return {"orderId": "ORD-1", "orderStatus": "CANCELLED"}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(
        order_placement=order_placement,
        get_order_status=get_order_status,
        get_order_detail=get_order_detail,
        cancel_order=cancel_order,
    )
    return broker, state


def _kernel(broker):
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    return k


class BuyOnFirstTick(Strategy):
    name = "buy_first"

    def __init__(self, quantity: int = 5):
        super().__init__()
        self.quantity = quantity
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=100.0)
            self.done = True


def _drive_order(k):
    """Full pipeline: a tick -> strategy signal -> risk -> OMS -> broker submit."""
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0,
                            quantity=1, ts=k.clock.now()))


def test_poll_emits_order_updated_on_status_change():
    broker, state = _broker_with_status_flow()
    k = _kernel(broker)
    _drive_order(k)
    ex = k.broker_execution()
    ex.poll()  # -> PARTIALLY_FILLED
    updates = [e for e in k.bus.history if isinstance(e, OrderUpdatedEvent)]
    assert len(updates) == 1
    assert updates[0].status == "PARTIALLY_FILLED"
    assert updates[0].filled_qty == 2
    ex.poll()  # -> COMPLETED
    updates = [e for e in k.bus.history if isinstance(e, OrderUpdatedEvent)]
    assert len(updates) == 2
    assert updates[1].status == "COMPLETED"


def test_cancel_marks_order_and_updates_status():
    broker, state = _broker_with_status_flow()
    k = _kernel(broker)
    _drive_order(k)
    ex = k.broker_execution()
    ex.poll()  # PARTIALLY_FILLED first, so order is tracked as open
    ex.cancel("ORD-1")
    ex.poll()  # CANCELLED
    updates = [e for e in k.bus.history if isinstance(e, OrderUpdatedEvent)]
    assert any(u.status == "CANCELLED" for u in updates)
