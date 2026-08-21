"""OrderEngine must materialize the signal's DECLARED order_type.

Regression: a MARKET signal with a nonzero price (strategies carry the bar
close as `price` for risk checks) was silently materialized as LIMIT at that
price — backtest filled at the signal bar's open (look-ahead), live rested an
unfillable limit. The strategy's intent is the contract.
"""
from datetime import datetime

from ntrade.engines.order_engine import OrderEngine
from ntrade.events.risk import SignalApprovedEvent, SignalGeneratedEvent


class _SpyRouter:
    def __init__(self):
        self.intents = []

    def submit(self, intent):
        self.intents.append(intent)
        return None


def _engine(router) -> OrderEngine:
    engine = OrderEngine.__new__(OrderEngine)  # skip __init__'s bus subscribe
    engine.ctx = type("C", (), {"bus": type("B", (), {
        "publish": staticmethod(lambda *a: None)})()})()
    engine.router = router
    engine._intents = 0
    return engine


def _approved(order_type: str, price: float) -> SignalApprovedEvent:
    sig = SignalGeneratedEvent(
        symbol="NIFTY", exchange="NFO", side="BUY", quantity=1,
        price=price, order_type=order_type, strategy="t",
        metadata={"reference_price": price},
        ts=datetime(2026, 8, 21, 9, 15))
    return SignalApprovedEvent(signal=sig, ts=sig.ts)


def test_market_signal_with_price_stays_market():
    router = _SpyRouter()
    _engine(router).on_signal_approved(_approved("MARKET", 101.0))
    assert router.intents[0].order_type == "MARKET"
    assert router.intents[0].price == 101.0  # kept for risk/audit, NOT a limit
    assert router.intents[0].reference_price == 101.0


def test_limit_signal_stays_limit():
    router = _SpyRouter()
    _engine(router).on_signal_approved(_approved("LIMIT", 100.5))
    assert router.intents[0].order_type == "LIMIT"
    assert router.intents[0].price == 100.5
