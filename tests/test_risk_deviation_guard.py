"""A zero-priced MARKET signal must not be auto-rejected as 'deviates 100%'.

Regression: dev = |0 - ref|/ref*100 = 100% for every MARKET signal carrying
the documented price=0.0 convention — arming price_deviation_pct silently
disabled the strategy while looking like a cautious risk system.
"""
from datetime import datetime

from ntrade.events.risk import SignalGeneratedEvent
from ntrade.engines.risk_engine import RiskEngine


class _Inst:
    class market:
        @staticmethod
        def ltp():
            return 100.0

        @staticmethod
        def prev_close():
            return 0.0


def _kernel_ctx():
    from ntrade.kernel.event_bus import EventBus
    bus = EventBus()
    ctx = type("Ctx", (), {"bus": bus, "instrument": lambda s, sym: _Inst(),
                           "portfolio": type("P", (), {"positions": []})(),
                           "account": type("A", (), {"balance": 100_000.0})(),
                           "now": lambda s: datetime(2026, 8, 21, 9, 15)})()
    return ctx


def test_zero_priced_market_signal_not_deviation_rejected():
    risk = RiskEngine(_kernel_ctx(), price_deviation_pct=2.0)
    sig = SignalGeneratedEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                               quantity=1, price=0.0, order_type="MARKET",
                               strategy="s", ts=datetime(2026, 8, 21, 9, 15))
    risk.on_signal(sig)
    assert risk.approved == 1, risk.halt_reason


def test_priced_signal_still_deviation_checked():
    risk = RiskEngine(_kernel_ctx(), price_deviation_pct=2.0)
    sig = SignalGeneratedEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                               quantity=1, price=120.0, order_type="LIMIT",
                               strategy="s", ts=datetime(2026, 8, 21, 9, 15))
    risk.on_signal(sig)
    assert risk.rejected == 1
