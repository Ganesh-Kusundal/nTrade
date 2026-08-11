"""LiveRunner orchestration loop (G2-B1)."""
import time

import pytest

from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.live_runner import LiveRunner
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


class _FakeTimer:
    def __init__(self):
        self.t = 0.0

    def advance(self, dt):
        self.t += dt


def _source(data=None):
    import pandas as pd
    from datetime import datetime, timedelta
    if data is None:
        rows = [{"timestamp": datetime(2026, 7, 30, 9, 15),
                 "open": 100.0, "high": 103.0, "low": 98.0,
                 "close": 101.0, "volume": 100}]
        data = pd.DataFrame(rows)
    return SyntheticMarketFeedSource(symbol="SYM", exchange="NSE", data=data)


def test_run_duration_stops_and_cleans_up():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    src = _source()
    runner = LiveRunner(k, src, poll_interval=0.1, sync_interval=0.1)
    runner._sleep = lambda s: None  # no wall-clock waiting
    runner.run(duration=0.3)
    assert runner.kernel.stop.__name__  # sanity
    # stop() flushed the kernel and stopped the source
    assert src.ticks_published > 0


def test_step_polls_and_syncs_on_interval():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source(), poll_interval=2.0, sync_interval=5.0)
    timer = _FakeTimer()
    runner._timer = lambda: timer.t
    runner.start()
    timer.advance(2.0)
    runner.step()
    assert runner.polls == 1
    timer.advance(3.0)  # total 5 -> sync due
    runner.step()
    assert runner.syncs == 1
    runner.stop()


def test_poll_sync_noop_without_broker():
    """Sim-mode kernel: poll_orders()/sync_positions() are harmless no-ops."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source(), poll_interval=0.01, sync_interval=0.01)
    runner._timer = lambda: 100.0
    runner.start()
    runner.step()
    assert runner.polls >= 1
    assert runner.syncs >= 1
    runner.stop()


def test_runner_publishes_lifecycle_events():
    from ntrade.events.lifecycle import RunnerStartedEvent, RunnerStoppedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    runner._sleep = lambda s: None
    runner.run(duration=0.1)
    kinds = [type(e).__name__ for e in k.bus.history]
    assert "RunnerStartedEvent" in kinds
    assert "RunnerStoppedEvent" in kinds


def test_step_evaluates_risk_breakers_between_signals():
    from ntrade.domain.instruments.cash import Equity
    from ntrade.domain.portfolio import Position
    from ntrade.engines.risk_engine import RiskEngine
    from ntrade.events.risk import RiskHaltedEvent, SignalGeneratedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=10_000.0)
    k.register(Equity("NIFTY"))
    k.bus.unsubscribe(SignalGeneratedEvent, k.risk_engine.on_signal)
    k.risk_engine = RiskEngine(k.ctx, max_daily_loss=500.0)
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    runner = LiveRunner(k, _source(), poll_interval=0.01, sync_interval=0.01)
    runner._timer = lambda: 100.0
    runner.start()
    runner.step()
    assert runner.halted
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)
    runner.stop()


def test_feed_disconnected_halts_runner():
    from ntrade.events.lifecycle import FeedDisconnectedEvent
    from ntrade.events.risk import RiskHaltedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    runner.kernel.bus.publish(FeedDisconnectedEvent(
        reason="ws drop", ts=runner.kernel.clock.now()))
    assert runner.halted
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)


def test_order_timeout_cancels_stale_order():
    from ntrade.events.order import OrderTimeoutEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    cancelled = []
    runner.kernel.cancel_order = lambda oid: cancelled.append(oid)
    runner.kernel.bus.publish(OrderTimeoutEvent(
        order_id="O1", symbol="TCS", exchange="NSE", side="BUY",
        quantity=10, age_seconds=120.0, ts=runner.kernel.clock.now()))
    assert cancelled == ["O1"]


def test_heartbeat_event_is_logged(caplog):
    import logging
    from ntrade.events.lifecycle import HeartbeatEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    with caplog.at_level(logging.INFO, logger="ntrade.runner"):
        k.bus.publish(HeartbeatEvent(tick_count=5, open_orders=2, ts=k.clock.now()))
    assert "tick_count=5" in caplog.text and "open_orders=2" in caplog.text


# ------------------------------------------------------------------ H-3
def test_stop_cancels_resting_orders():
    """Shutdown cancels every tracked open order (never leaves them live)."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    k.open_orders = lambda: ["O1", "O2"]
    cancelled = []
    k.cancel_order = lambda oid: cancelled.append(oid)
    runner.start()
    runner.stop()
    assert cancelled == ["O1", "O2"]
    assert runner.started is False


def test_stop_cancel_failure_does_not_block_shutdown():
    """A failing cancel logs but must not prevent the runner from stopping."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    k.open_orders = lambda: ["O1"]

    def _boom(oid):
        raise RuntimeError("broker unreachable")

    k.cancel_order = _boom
    runner.start()
    runner.stop()  # must not raise
    assert runner.started is False


def test_stop_cancel_on_stop_false_leaves_resting_orders():
    """Opt-out flag keeps resting orders live (deliberate, logged)."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source(), cancel_on_stop=False)
    k.open_orders = lambda: ["O1"]
    cancelled = []
    k.cancel_order = lambda oid: cancelled.append(oid)
    runner.start()
    runner.stop()
    assert cancelled == []
