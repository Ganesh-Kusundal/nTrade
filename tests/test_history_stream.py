"""Tests for HistoricalSeries (dataframe-like, attached) and LiveStream."""

from datetime import timezone

import pandas as pd
import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.market.history import HistoricalSeries
from ntrade.domain.market.quote import Tick
from ntrade.domain.market.stream import LiveStream, SubscriptionState


def test_history_series_empty():
    rel = Equity("RELIANCE")
    h = HistoricalSeries(rel)
    assert len(h) == 0
    assert not h.cached
    assert not h.is_fresh()


def test_history_df_property_is_defensive_copy():
    """D-019: external readers of ``.df`` get a copy — in-place mutation via
    the property must not corrupt the instrument's cached frame."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    h = rel._history.fetch(timeframe="5m")
    leaked = h.df
    leaked.loc[0, "close"] = 999.0
    assert h.df.loc[0, "close"] != 999.0  # internal frame untouched


def test_history_clock_drives_freshness():
    """D-019: is_fresh/fetch follow the injected clock (replay parity)."""
    from datetime import datetime, timedelta

    class FakeClock:
        def __init__(self):
            self.t = datetime(2026, 8, 3, 9, 15, 0)

        def __call__(self):
            return self.t

    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    clock = FakeClock()
    h = HistoricalSeries(rel, clock=clock)
    h.fetch(timeframe="5m")
    assert h.is_fresh()
    clock.t += timedelta(minutes=6)
    assert not h.is_fresh()  # 6min > 5min window, per injected clock


def test_kernel_register_wires_history_clock():
    """D-019: registering an instrument on a kernel wires ctx.now into its
    history so replay mode drives freshness deterministically."""
    from ntrade.kernel.clock import ReplayClock
    from ntrade.kernel.event_bus import EventBus
    from ntrade.kernel.context import TradingContext

    rel = Equity("RELIANCE")
    ctx = TradingContext(EventBus(), ReplayClock())
    ctx.register(rel)
    # Bound methods are recreated per access, so compare behaviour, not identity.
    assert rel._history.clock() == ctx.now()
    assert callable(rel._history.clock)
    assert rel._history.clock.__self__ is ctx


def test_history_fetch_sets_state():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    h = rel._history.fetch(timeframe="5m")
    assert h.cached
    assert h.is_fresh()
    assert h.last_fetched_at is not None


def test_history_download_forces_fresh():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    first = rel._history.fetch(timeframe="5m")
    downloaded = rel._history.download(timeframe="5m")
    assert downloaded is not first or downloaded.cached


def test_history_to_df_copy():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    h = rel._history.fetch(timeframe="5m")
    df = h.to_df()
    df.loc[0, "close"] = 999.0
    assert h.df.loc[0, "close"] != 999.0  # copy is independent


def test_history_resample():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    h = rel._history.fetch(timeframe="5m")
    coarse = h.resample("15min")
    assert coarse.timeframe == "15min"
    assert len(coarse) <= len(h)
    assert {"timestamp", "open", "high", "low", "close"} <= set(coarse.df.columns)


def test_resample_labels_match_candle_engine_buckets():
    """K-025: HistoricalSeries.resample must label bars at the same (end-of-
    bar, right-edge) boundary as CandleEngine's closed-candle labels.

    CandleEngine._bucket floors to a UTC-pinned epoch (closed-LEFT bin
    membership) and labels each closed candle at ``bucket + seconds`` (the
    RIGHT edge). pandas resample defaults to ``label="left"`` (bin START), so
    a 1m->5m resample labels 09:15 while the engine labels 09:20. The parity
    test asserts resample labels equal the engine's end-of-bar labels for the
    same timestamps.
    """
    from datetime import datetime, timedelta

    from ntrade.engines.candle_engine import CandleEngine
    from ntrade.kernel.event_bus import EventBus

    bus = EventBus()
    ctx = type("Ctx", (), {"bus": bus})()
    engine = CandleEngine(ctx, timeframe="5m")

    start = datetime(2026, 8, 3, 9, 15)  # naive UTC wall-clock
    ts = [start + timedelta(minutes=i) for i in range(10)]  # 09:15..09:24
    df = pd.DataFrame({
        "timestamp": ts,
        "open": [100.0] * 10, "high": [101.0] * 10,
        "low": [99.0] * 10, "close": [100.5] * 10,
        "volume": [100] * 10,
    })
    h = HistoricalSeries(Equity("RELIANCE"), df, timeframe="1m")
    coarse = h.resample("5min")
    # Engine end-of-bar labels for the same timestamps: bucket + 300s. The
    # engine pins naive timestamps to UTC, matching the naive index here.
    engine_labels = [
        datetime.fromtimestamp(engine._bucket(t) + engine.seconds,
                               tz=timezone.utc).replace(tzinfo=None)
        for t in (start, start + timedelta(minutes=5))
    ]
    got = list(coarse.df["timestamp"])
    assert len(got) == 2, f"expected 2 resampled bars, got {got}"
    assert all(t.tzinfo is None for t in got), "resample index must stay timezone-naive"
    assert got == engine_labels, f"resample labels {got} != engine end-of-bar {engine_labels}"


def test_resample_implementations_share_right_edge_convention():
    """Cross-path parity (K-025 + F-001): HistoricalSeries.resample and
    DhanMapper.resample_history must both label bars at the bin's RIGHT edge.

    For 3m the two grids coincide (09:15 sits on both the epoch 180s grid and
    the 09:15-IST origin grid), so bars AND labels are identical. For 2m/4m
    the F-001 09:15-IST origin anchors a session grid offset from the epoch
    grid (09:15 % 120s = 60s, % 240s = 180s), so the bars legitimately differ
    — only the right-edge convention is shared, and the offset is the
    documented cost of night-session containment.
    """
    from datetime import datetime, timedelta

    from ntrade.brokers.dhan_mapper import DhanMapper

    start = datetime(2026, 8, 3, 9, 15)  # naive wall clock
    ts = [start + timedelta(minutes=i) for i in range(12)]  # 09:15..09:26
    df = pd.DataFrame({
        "timestamp": ts,
        "open": [100 + i for i in range(12)],
        "high": [101 + i for i in range(12)],
        "low": [99 + i for i in range(12)],
        "close": [100.5 + i for i in range(12)],
        "volume": [100] * 12,
    })
    h = HistoricalSeries(Equity("RELIANCE"), df, timeframe="1m")

    # 3m: identical bars AND identical right-edge labels on both grids.
    a = h.resample("3min").df
    b = DhanMapper.resample_history(df, "3min")
    assert list(pd.to_datetime(a["timestamp"])) == list(pd.to_datetime(b["timestamp"]))
    for col in ("open", "high", "low", "close", "volume"):
        assert list(a[col]) == list(b[col]), f"3m {col} diverged between resample paths"

    # 2m/4m: grids intentionally diverge — each must still label at the right
    # edge of its OWN grid (epoch-anchored for HistoricalSeries, 09:15-anchored
    # for F-001), locking in the documented offset.
    assert list(pd.to_datetime(h.resample("2min").df["timestamp"])) == [
        pd.Timestamp("2026-08-03 09:16:00"), pd.Timestamp("2026-08-03 09:18:00"),
        pd.Timestamp("2026-08-03 09:20:00"), pd.Timestamp("2026-08-03 09:22:00"),
        pd.Timestamp("2026-08-03 09:24:00"), pd.Timestamp("2026-08-03 09:26:00"),
        pd.Timestamp("2026-08-03 09:28:00"),
    ]
    assert list(pd.to_datetime(DhanMapper.resample_history(df, "2min")["timestamp"])) == [
        pd.Timestamp("2026-08-03 09:17:00"), pd.Timestamp("2026-08-03 09:19:00"),
        pd.Timestamp("2026-08-03 09:21:00"), pd.Timestamp("2026-08-03 09:23:00"),
        pd.Timestamp("2026-08-03 09:25:00"), pd.Timestamp("2026-08-03 09:27:00"),
    ]
    assert list(pd.to_datetime(h.resample("4min").df["timestamp"])) == [
        pd.Timestamp("2026-08-03 09:16:00"), pd.Timestamp("2026-08-03 09:20:00"),
        pd.Timestamp("2026-08-03 09:24:00"), pd.Timestamp("2026-08-03 09:28:00"),
    ]
    assert list(pd.to_datetime(DhanMapper.resample_history(df, "4min")["timestamp"])) == [
        pd.Timestamp("2026-08-03 09:19:00"), pd.Timestamp("2026-08-03 09:23:00"),
        pd.Timestamp("2026-08-03 09:27:00"),
    ]


def test_history_live_merge_preserves_schema():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    h = rel._history.fetch(timeframe="5m")
    before = len(h)
    broker.push_tick(rel, 2500.0)
    h.live_merge()
    # Must not leak raw-tick columns (symbol/side/kind) into the OHLCV frame.
    expected = {"timestamp", "open", "high", "low", "close", "volume"}
    assert expected <= set(h.df.columns)
    assert not {"symbol", "side", "kind"} & set(h.df.columns)
    assert len(h) >= before
    assert h.df["close"].iloc[-1] == 2500.0  # live print is in the merged row


def test_stream_states():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    assert rel._stream.state == SubscriptionState.NOT_SUBSCRIBED
    rel.stream.subscribe()
    assert rel._stream.state == SubscriptionState.SUBSCRIBED
    assert rel.stream.is_live
    rel.stream.unsubscribe()
    assert not rel.stream.is_live


def test_stream_ticks_and_handlers():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    seen = []
    rel._stream.on_tick(lambda t: seen.append(t))
    rel._stream.on_quote(lambda t: seen.append(("quote", t)))
    rel.stream.subscribe()
    broker.push_tick(rel, 2510.0)
    assert len(seen) == 2  # tick + quote
    assert rel._stream.tick_count == 1
    assert rel._stream.ticks()[0].price == 2510.0
    assert not rel._stream.live_ticks_df.empty


def test_stream_unknown_event_raises():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    with pytest.raises(ValueError):
        rel._stream.on("bogus_event", lambda x: None)


def test_stream_callback_exception_is_swallowed():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)

    def boom(_):
        raise RuntimeError("handler blew up")

    rel._stream.on_tick(boom)
    rel.stream.subscribe()
    broker.push_tick(rel, 100.0)  # must not raise
    assert rel._stream.tick_count == 1


def test_ingest_depth_tick():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    seen = []
    rel._stream.on_depth(lambda t: seen.append(t))
    rel.stream.subscribe()
    tick = Tick(symbol="RELIANCE", price=100.0, kind="depth")
    rel._stream.ingest_tick(tick)
    assert len(seen) == 1
