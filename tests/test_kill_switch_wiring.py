"""Kill-switch wiring: RiskHaltedEvent -> Dhan kill_switch capability (G2-C2)."""
import types
from datetime import datetime

import pandas as pd

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.events.risk import RiskHaltedEvent
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.live_runner import LiveRunner
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


def _make_broker():
    calls = {"kill": 0}

    def kill_switch(action="DEACTIVATE", **kw):
        calls["kill"] += 1
        return {"action": action}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(kill_switch=kill_switch)
    return broker, calls


def _frame():
    return pd.DataFrame([{
        "timestamp": datetime(2026, 7, 30, 9, 15),
        "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0, "volume": 100,
    }])


def test_risk_halt_triggers_kill_switch_on_broker_instruments():
    broker, calls = _make_broker()
    k = TradingKernel(mode="live", clock=LiveClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY"))
    k.ctx.instruments["NIFTY"]._broker = broker  # instrument carries the broker
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=_frame())
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    runner.start()
    k.bus.publish(RiskHaltedEvent(reason="test halt", ts=k.clock.now()))
    assert calls["kill"] >= 1
    runner.stop()


def test_no_broker_no_crash():
    k = TradingKernel(mode="replay", clock=LiveClock(), timeframe="1m")
    k.register(Equity("NIFTY"))
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=_frame())
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    runner.start()
    k.bus.publish(RiskHaltedEvent(reason="test halt", ts=k.clock.now()))  # must not raise
    runner.stop()


def test_kill_switch_failure_is_flagged_and_logged():
    broker, calls = _make_broker()
    broker.tsl.kill_switch = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    k = TradingKernel(mode="live", clock=LiveClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY"))
    k.ctx.instruments["NIFTY"]._broker = broker
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=_frame())
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    runner.start()
    k.bus.publish(RiskHaltedEvent(reason="test halt", ts=k.clock.now()))
    assert runner.kill_switch_failed is True
    assert runner.kill_switched is False
    runner.stop()


def test_risk_resume_deactivates_kill_switch():
    """RiskResumedEvent -> kill_switch DEACTIVATE re-arms the broker."""
    from ntrade.events.risk import RiskResumedEvent

    actions = []

    def kill_switch(action="DEACTIVATE", **kw):
        actions.append(action)
        return {"action": action}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(kill_switch=kill_switch)
    k = TradingKernel(mode="live", clock=LiveClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY"))
    k.ctx.instruments["NIFTY"]._broker = broker
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=_frame())
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    runner.start()

    k.bus.publish(RiskHaltedEvent(reason="test halt", ts=k.clock.now()))
    assert actions == ["ACTIVATE"]
    assert runner.halted and runner.kill_switched

    k.bus.publish(RiskResumedEvent(ts=k.clock.now()))
    assert actions == ["ACTIVATE", "DEACTIVATE"]
    assert runner.halted is False
    assert runner.kill_switched is False
    runner.stop()


def test_risk_resume_deactivate_failure_keeps_kill_switched():
    """If DEACTIVATE fails, kill_switched stays True (broker still halted)."""
    from ntrade.events.risk import RiskResumedEvent

    def kill_switch(action="DEACTIVATE", **kw):
        if action == "DEACTIVATE":
            raise RuntimeError("boom")
        return {"action": action}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(kill_switch=kill_switch)
    k = TradingKernel(mode="live", clock=LiveClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY"))
    k.ctx.instruments["NIFTY"]._broker = broker
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=_frame())
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    runner.start()

    k.bus.publish(RiskHaltedEvent(reason="test halt", ts=k.clock.now()))
    assert runner.kill_switched is True
    k.bus.publish(RiskResumedEvent(ts=k.clock.now()))
    assert runner.kill_switched is True   # broker still halted
    assert runner.kill_switch_failed is True
    runner.stop()


def test_feed_watchdog_warns_on_stale_ticks():
    from ntrade.runner.live_runner import LiveRunner
    from ntrade.kernel.session import TradingKernel
    from ntrade.kernel.clock import LiveClock
    from ntrade.domain.instruments.cash import Equity
    from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource
    import pandas as pd

    class _Broker:
        def get_quote(self, inst):
            from ntrade.domain.market.quote import Quote
            from datetime import datetime
            return Quote(ltp=100.0, bid=100.0, ask=100.0, timestamp=datetime.now())
        def get_depth(self, inst):
            return None

    broker = _Broker()
    k = TradingKernel(mode="live", clock=LiveClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY"))
    k.ctx.instruments["NIFTY"]._broker = broker
    frame = pd.DataFrame({
        "timestamp": [k.clock.now()],
        "open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5],
        "volume": [1000],
    })
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=frame)
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    # Run 3 steps without any ticks — watchdog should increment
    runner.start()
    runner.step()
    runner.step()
    runner.step()
    assert runner._watchdog_missed >= 1
    runner.stop()
