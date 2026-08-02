"""Tests about the LiveRunner's feed-drop and order-timeout consumers being
independent (contract Items B+C): a feed drop halts (risk kill-switch) without
cancelling orders; an order timeout cancels without halting.
"""
import pandas as pd
from datetime import datetime

from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.live_runner import LiveRunner
from ntrade.events.lifecycle import FeedDisconnectedEvent
from ntrade.events.order import OrderTimeoutEvent
from ntrade.events.risk import RiskHaltedEvent
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


def _replay_runner():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    data = pd.DataFrame([{"timestamp": datetime(2026, 7, 30, 9, 15),
                          "open": 100.0, "high": 103.0, "low": 98.0,
                          "close": 101.0, "volume": 100}])
    src = SyntheticMarketFeedSource(symbol="SYM", exchange="NSE", data=data)
    runner = LiveRunner(k, src)
    return k, runner


def test_feed_drop_triggers_risk_halt_not_order_cancel():
    k, runner = _replay_runner()
    halted, cancelled = [], []
    k.bus.subscribe(RiskHaltedEvent, halted.append)
    runner.kernel.cancel_order = lambda oid: cancelled.append(oid)

    runner._on_feed_disconnected(FeedDisconnectedEvent(
        reason="ws drop", ts=runner.kernel.clock.now()))

    assert len(halted) == 1          # B: feed-drop => risk halt (kill switch)
    assert cancelled == []           # B: and does NOT cancel orders
    assert runner.halted is True


def test_order_timeout_cancels_but_does_not_halt():
    k, runner = _replay_runner()
    halted, cancelled = [], []
    k.bus.subscribe(RiskHaltedEvent, halted.append)
    runner.kernel.cancel_order = lambda oid: cancelled.append(oid)
    assert runner.halted is False

    runner._on_order_timeout(OrderTimeoutEvent(
        order_id="O1", symbol="TCS", exchange="NSE", side="BUY",
        quantity=10, age_seconds=120.0, ts=runner.kernel.clock.now()))

    assert cancelled == ["O1"]       # C: order timeout cancels the stale order
    assert len(halted) == 0          # C: and does NOT halt the runner
    assert runner.halted is False