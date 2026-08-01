"""Tests for the instrument hierarchy, state ownership and behaviors."""

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Commodity, Equity, Index, Spot
from ntrade.domain.instruments.derivatives import Future, Option
from ntrade.domain.market.quote import Tick


def test_equity_is_instrument():
    nifty = Equity("NIFTY")
    assert nifty.symbol == "NIFTY"
    assert nifty.exchange == "NSE"
    assert nifty.KIND == "equity"
    assert nifty.market.quote() is not None
    assert nifty.market.history() is not None
    assert nifty._stream is not None


def test_instrument_state_ownership():
    nifty = Equity("NIFTY")
    nifty._quote = nifty._quote.with_update(ltp=100.0, bid=99.9, ask=100.1, volume=1000)
    assert nifty.market.ltp() == 100.0
    assert nifty.market.bid() == 99.9
    assert nifty.market.ask() == 100.1
    assert nifty.market.volume() == 1000
    assert nifty.market.spread() == 0.2
    assert nifty.market.mid_price() == 100.0


def test_refresh_via_paper_broker():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    assert rel.market.ltp() == 0.0
    rel.refresh()
    assert rel.market.ltp() == 2500.0
    assert rel.last_refresh_at is not None


def test_subscription_lifecycle():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    assert not rel.stream.is_live
    rel.stream.subscribe()
    assert rel.stream.is_live
    assert rel._stream.is_subscribed
    rel.stream.unsubscribe()
    assert not rel.stream.is_live


def test_live_tick_handlers():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    received = []
    rel.stream.on_tick(lambda tick: received.append(tick))
    rel.stream.subscribe()
    broker.push_tick(rel, 2510.0, side="buy")
    assert len(received) == 1
    assert received[0].price == 2510.0
    assert rel.stream.last_tick is not None
    assert rel.market.ltp() == 2510.0


def test_history_fetch_and_delegate():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    series = rel._history.fetch(timeframe="5m")
    assert len(series) == 200
    assert series.cached
    # dataframe-like delegation
    assert hasattr(series, "close")
    assert len(series["close"]) == 200


def test_history_freshness_cache():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    first = rel._history.fetch(timeframe="5m")
    assert first.is_fresh()
    # second fetch returns cached (no re-download needed)
    again = rel._history.fetch(timeframe="5m")
    assert again is first


def test_history_cache_is_per_timeframe():
    """Regression: a fresh 5m cache must not be served for a 1d request."""
    broker = PaperBroker()
    broker.seed_history("RELIANCE", rows=100, timeframe="5m")
    broker.seed_history("RELIANCE", rows=300, timeframe="1d")
    rel = Equity("RELIANCE", broker=broker)

    five = rel._history.fetch(timeframe="5m")
    assert len(five) == 100
    daily = rel._history.fetch(timeframe="1d")
    # the fresh 5m cache is still valid, but NOT for a different timeframe
    assert len(daily) == 300  # re-fetched 1d, not served the 100-row 5m cache
    assert daily.timeframe == "1d"
    # switching back re-fetches the 5m data (not the 1d cache)
    again = rel._history.fetch(timeframe="5m")
    assert len(again) == 100
    assert again.timeframe == "5m"


def test_indicators_bundle():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    rel._history.fetch(timeframe="5m")
    rel.analytics.compute()
    assert "rsi_14" in rel.analytics.indicators
    assert "atr_14" in rel.analytics.indicators
    assert "vwap" in rel.analytics.indicators
    assert "stx_10_3" in rel.analytics.indicators


def test_index_and_commodity_defaults():
    idx = Index("NIFTY")
    assert idx.exchange == "INDEX"
    com = Commodity("CRUDEOIL")
    assert com.exchange == "MCX"
    spot = Spot("USDINR")
    assert spot.KIND == "spot"


def test_metadata_tags_annotations():
    nifty = Equity("NIFTY", sector="index")
    assert nifty._metadata["sector"] == "index"
    nifty.tag("core").annotate("note", "test")
    assert "core" in nifty.tags
    assert nifty.annotations["note"] == "test"


def test_clone_independent_state():
    nifty = Equity("NIFTY")
    nifty._quote = nifty._quote.with_update(ltp=100.0)
    clone = nifty.clone()
    assert clone.symbol == "NIFTY"
    assert clone.market.ltp() == 0.0  # fresh state
    clone._quote = clone._quote.with_update(ltp=200.0)
    assert nifty.market.ltp() == 100.0  # unaffected


def test_serialize_snapshot():
    nifty = Equity("NIFTY")
    nifty._quote = nifty._quote.with_update(ltp=100.0)
    snap = nifty.snapshot()
    assert snap["symbol"] == "NIFTY"
    assert snap["kind"] == "equity"
    assert snap["quote"]["ltp"] == 100.0


def test_option_requires_valid_type():
    with pytest.raises(ValueError):
        Option("X", strike=100, expiry=None, option_type="XX", underlying_symbol="Y")


def test_future_basis():
    spot = Equity("RELIANCE")
    spot._quote = spot._quote.with_update(ltp=2500.0)
    fut = Future("RELIANCE FUT", underlying="RELIANCE")
    fut.set_underlying(spot)
    fut._quote = fut._quote.with_update(ltp=2510.0)
    assert fut.basis() == 10.0
