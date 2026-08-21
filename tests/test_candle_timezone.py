"""Candle bucketing: naive timestamps are IST wall time; labels are naive UTC.

The single tz boundary is CandleEngine._bucket — live ticks arrive IST-aware
(converted by astimezone), backtest/replay bars arrive naive-IST (converted
here), and every downstream consumer sees identical UTC-epoch buckets for the
same session minute regardless of host TZ.
"""
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


@pytest.fixture
def force_tz(monkeypatch):
    """Run a test under a specific process timezone, then restore it."""
    saved = os.environ.get("TZ")

    def set_tz(tz: str) -> str:
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        return tz

    yield set_tz
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def test_bucket_epoch_is_pinned_to_utc(force_tz):
    """A naive ts (IST wall time) must bucket to the UTC epoch of that instant.
    09:15 IST == 03:45 UTC. WAS: ts.timestamp() interpreted the naive ts in
    the process-local TZ."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    ts = datetime(2026, 1, 1, 9, 15)
    expected = int(ts.replace(tzinfo=ZoneInfo("Asia/Kolkata")).timestamp())
    expected -= expected % k.candle_engine.seconds
    assert k.candle_engine._bucket(ts) == expected


def test_closed_candle_label_matches_utc_wall_clock(force_tz):
    """The closed label must be the naive UTC wall-clock minute after the last
    tick. An IST 09:15-09:16 candle carries the UTC label 03:46. Guards
    against a half-fix that pins _bucket but leaves _close local."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("NIFTY"))
    ts = datetime(2026, 1, 1, 9, 15)
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=ts))
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=101.0,
                            ts=ts + timedelta(seconds=1)))
    k.candle_engine.flush()
    assert k.candle_engine.candles("NIFTY")[0].ts == datetime(2026, 1, 1, 3, 46)


def test_naive_ts_is_ist_wall_time(force_tz):
    """Naive timestamps are IST wall time (the storage + backtest convention).
    09:15 IST must bucket at the 03:45 UTC epoch — not be pinned as UTC."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    naive = datetime(2026, 1, 1, 9, 15)                      # IST wall time
    aware = naive.replace(tzinfo=ZoneInfo("Asia/Kolkata"))   # same instant
    assert k.candle_engine._bucket(naive) == k.candle_engine._bucket(aware)


def test_backtest_and_live_ticks_bucket_identically():
    """The parity point: the same session minute produces the same bucket
    whether it arrives naive-IST (backtest bar) or IST-aware (live tick)."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    naive = datetime(2026, 1, 1, 9, 15)
    aware = naive.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    assert k.candle_engine._bucket(naive) == k.candle_engine._bucket(aware)


def test_orb_reads_utc_labels_as_ist_session_minutes():
    """ORB's session windows are IST wall-clock. Kernel candle labels are
    naive UTC, so a 09:15 IST candle arrives as 03:45 — _ist_dt must convert,
    not relabel, or ORB trades the wrong window."""
    from ntrade.engines.orb_vwap import _ist_dt

    ist = _ist_dt(datetime(2026, 1, 1, 3, 45))   # kernel label (naive UTC)
    assert ist is not None and ist.hour == 9 and ist.minute == 15
    aware = _ist_dt(datetime(2026, 1, 1, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")))
    assert aware.hour == 9 and aware.minute == 15


def test_candle_engine_bounds_closed_candles():
    from ntrade.engines.candle_engine import CandleEngine
    from ntrade.events.market import TickEvent
    from ntrade.kernel.event_bus import EventBus
    from datetime import datetime

    bus = EventBus()
    ctx = type("Ctx", (), {"bus": bus})()
    engine = CandleEngine(ctx, timeframe="1m", max_candles=5)
    # Simulate ticks in 10 different minute buckets — each new minute closes the prior candle
    for i in range(10):
        ts = datetime(2026, 1, 1, 9, i + 1, 0)
        event = TickEvent(ts=ts, symbol="X", exchange="NSE", price=100.0 + i)
        engine.on_tick(event)
    # 9 candles closed (tick 2 closes candle 1, ..., tick 10 closes candle 9), bounded to 5
    assert len(engine._closed.get("X", [])) <= 5


def test_late_tick_does_not_fork_the_candle(force_tz):
    """A reordered websocket frame must be dropped, not close the live candle
    and open a phantom one from the past."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("NIFTY"))
    m1 = datetime(2026, 1, 1, 9, 15)
    m2 = datetime(2026, 1, 1, 9, 16)
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=m1))
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=101.0, ts=m2))
    # late tick for minute 1 — arrives after minute 2 started
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=99.0, ts=m1))
    k.candle_engine.flush()
    candles = k.candle_engine.candles("NIFTY")
    # m1 closed by the m2 tick; m2 flushed by stop(). The late 99.0 must not
    # have forked the series: exactly 2 candles, neither touched by 99.0.
    assert len(candles) == 2
    assert candles[0].low == 100.0
    assert candles[1].low == 101.0
    assert all(c.low > 99.0 for c in candles)
    assert k.candle_engine.late_ticks == 1
