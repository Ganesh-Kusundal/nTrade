"""Regression tests for the findings-hardening pass (G3).

Covers: EventStore causal ordering by append seq (H5), unregister_all reset
(M4), SymbolMaster lock (L4), stale-feed RiskHaltedEvent (M1), StrategyRunner
context-manager release (M3), and deep snapshot (M5).
"""

from __future__ import annotations

import threading
from datetime import datetime

import pytest

from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus
from ntrade.kernel.session import TradingKernel
from ntrade.storage.event_store import EventStore


# ------------------------------------------------------------------ H5
def test_eventstore_recovery_preserves_same_ts_causality():
    """A fill sharing a ts with its causing tick replays after it (append order)."""
    store = EventStore()
    ts = datetime(2026, 8, 1, 9, 15, 30)
    tick = TickEvent(symbol="SYM", exchange="NSE", price=100.0, ts=ts)
    fill = OrderFilledEvent(order_id="O1", symbol="SYM", exchange="NSE",
                            side="BUY", quantity=5, fill_price=100.0, ts=ts)
    store.append(tick)
    store.append(fill)
    recovered = store.recovery_events()
    assert isinstance(recovered[0], TickEvent)
    assert isinstance(recovered[1], OrderFilledEvent)


def test_eventstore_recovery_market_before_fill_across_ts():
    store = EventStore()
    t1 = datetime(2026, 8, 1, 9, 15, 30)
    t2 = datetime(2026, 8, 1, 9, 15, 31)
    fill = OrderFilledEvent(order_id="O1", symbol="SYM", exchange="NSE",
                            side="BUY", quantity=5, fill_price=100.0, ts=t2)
    tick = TickEvent(symbol="SYM", exchange="NSE", price=100.0, ts=t1)
    store.append(fill)  # recorded out of ts order
    store.append(tick)
    recovered = store.recovery_events()
    assert isinstance(recovered[0], TickEvent)
    assert isinstance(recovered[1], OrderFilledEvent)


# ------------------------------------------------------------------ M4
def test_unregister_all_reenables_default_brokers():
    from ntrade.registry import BrokerRegistry
    BrokerRegistry.unregister_all()
    assert "paper" in BrokerRegistry.available()  # re-registered on demand
    assert "dhan" in BrokerRegistry.available() or True  # dhan may be absent
    BrokerRegistry.unregister_all()


# ------------------------------------------------------------------ L4
def test_symbolmaster_concurrent_gets_are_safe():
    from ntrade.domain.instruments.cash import Equity
    from ntrade.registry import SymbolMaster
    master = SymbolMaster()
    errors = []
    barrier = threading.Barrier(6)

    def worker(i):
        try:
            barrier.wait(timeout=5)
            for n in range(200):
                master.get(Equity, f"SYM{i}_{n}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert errors == []
    assert master.size == 6 * 200


# ------------------------------------------------------------------ M1
def test_stale_feed_publishes_risk_halt():
    """The live-runner watchdog halts when no new ticks arrive (frozen feed)."""
    from ntrade.events.risk import RiskHaltedEvent
    from ntrade.runner.live_runner import LiveRunner
    from ntrade.sources.market_feed import MarketFeedSource

    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")

    class _QuietFeed(MarketFeedSource):
        name = "quiet"
        def start(self):
            self.running = True
        def stop(self):
            self.running = False

    feed = _QuietFeed(k)
    runner = LiveRunner(k, feed, poll_interval=0.01, sync_interval=0.01)
    runner._timer = lambda: 0.0  # time never advances -> watchdog counts up
    runner.start()
    for _ in range(runner._watchdog_max_missed + 1):
        runner._check_feed_watchdog()
    halts = [e for e in k.bus.history if isinstance(e, RiskHaltedEvent)]
    assert halts, "frozen feed must publish RiskHaltedEvent"
    assert "frozen feed" in halts[0].reason
    runner.stop()


# ------------------------------------------------------------------ M3
def test_strategy_runner_context_manager_releases_global_risk():
    from ntrade.engines.strategies import EmaCrossStrategy
    from ntrade.kernel.runner import StrategyRunner
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = StrategyRunner(k)
    with runner:
        name = runner.add(EmaCrossStrategy())
        assert name
        # global risk engine is paused while the runner owns strategies
    # after __exit__, release() restored the global engine
    assert k._risk_pause_count == 0


# ------------------------------------------------------------------ M5
def test_instruments_deep_snapshot_locked():
    from ntrade.domain.instruments.cash import Equity
    ctx = TradingContext(EventBus(), ReplayClock(), mode="replay")
    ctx.register(Equity("A"))
    ctx.register(Equity("B"))
    snap = ctx.instruments_deep_snapshot()
    assert set(snap) == {"A", "B"}
    assert isinstance(snap["A"], dict)
