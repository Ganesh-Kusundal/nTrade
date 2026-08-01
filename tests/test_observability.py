"""Observability: broker lifecycle logged (G2-E1)."""
import logging
import types

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategy_engine import Strategy
from ntrade.events.market import TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


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


def test_broker_fill_is_logged(caplog):
    state = {"stage": "pending"}

    def order_placement(**kw):
        return "ORD-1"

    def get_order_status(orderid=None, **kw):
        if state["stage"] == "pending":
            state["stage"] = "partial"
            return "PARTIAL"
        state["stage"] = "complete"
        return "COMPLETE"

    def get_order_detail(orderid=None, **kw):
        filled = 2 if state["stage"] == "partial" else 5
        return {"orderId": orderid, "orderStatus": "COMPLETE",
                "filledQty": filled, "avgPrice": 100.0}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(
        order_placement=order_placement,
        get_order_status=get_order_status,
        get_order_detail=get_order_detail,
    )
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0,
                            quantity=1, ts=k.clock.now()))
    with caplog.at_level(logging.INFO, logger="ntrade.execution"):
        k.poll_orders()
        k.poll_orders()
    assert any("fill" in r.message.lower() for r in caplog.records)
