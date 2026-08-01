"""Kernel OMS passthroughs (G2-D2)."""
import types

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.engines.strategy_engine import Strategy
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


def _kernel():
    calls = {"cancel": 0, "modify": 0}

    def cancel_order(**kw):
        calls["cancel"] += 1
        return {"orderId": "ORD-9", "orderStatus": "CANCELLED"}

    def modify_order(**kw):
        calls["modify"] += 1
        return {"orderId": "ORD-9"}

    def get_order_status(orderid=None, **kw):
        return "PENDING"

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(
        order_placement=lambda **kw: "ORD-9",
        get_order_status=get_order_status,
        cancel_order=cancel_order,
        modify_order=modify_order,
    )
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    return k, calls


def test_modify_cancel_delegate_to_broker_execution():
    k, calls = _kernel()
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0,
                            quantity=1, ts=k.clock.now()))
    assert k.open_orders() == ["ORD-9"]
    k.modify_order("ORD-9", price=101.0)
    k.cancel_order("ORD-9")
    assert calls["modify"] == 1
    assert calls["cancel"] == 1


def test_sim_mode_passthroughs_are_noops():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    assert k.open_orders() == []
    assert k.modify_order("x") is None
    assert k.cancel_order("x") is None
