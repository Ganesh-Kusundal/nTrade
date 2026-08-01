"""Tests for HistoricalSeries (dataframe-like, attached) and LiveStream."""

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
