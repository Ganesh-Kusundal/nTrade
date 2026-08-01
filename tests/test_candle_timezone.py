"""M2 fix: candle bucketing is pinned to UTC, independent of host TZ (G2-F4)."""
import os
import time
from datetime import datetime, timedelta, timezone

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
    """A naive ts must bucket to the UTC-pinned epoch. WAS: ts.timestamp()
    interpreted the naive ts in the process-local TZ (IST buckets 5.5h away)."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    ts = datetime(2026, 1, 1, 9, 15)
    expected = int(ts.replace(tzinfo=timezone.utc).timestamp())
    expected -= expected % k.candle_engine.seconds
    assert k.candle_engine._bucket(ts) == expected


def test_closed_candle_label_matches_utc_wall_clock(force_tz):
    """The closed label must be the naive UTC wall-clock minute after the last
    tick. Guards against a half-fix that pins _bucket but leaves _close local
    (which would shift the label by the TZ offset)."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("NIFTY"))
    ts = datetime(2026, 1, 1, 9, 15)
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=ts))
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=101.0,
                            ts=ts + timedelta(seconds=1)))
    k.candle_engine.flush()
    assert k.candle_engine.candles("NIFTY")[0].ts == datetime(2026, 1, 1, 9, 16)


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
