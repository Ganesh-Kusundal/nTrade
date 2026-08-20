"""Tests for mission API behaviors: session(), detect_*, statistics, supertrend,
candles/ticks/stream accessors, Future.cost_of_carry / continuous."""

from datetime import date, timedelta

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.instruments.derivatives import Future
from ntrade.domain.market.depth import DepthLevel, MarketDepth
from ntrade.domain.session import MarketState, SessionState


def test_session_is_callable():
    """The session() method must be callable (attribute must not shadow it)."""
    rel = Equity("RELIANCE")
    sess = rel.session()
    assert isinstance(sess, SessionState)
    assert not sess.is_open
    rel.set_market_status(MarketState.OPEN)
    assert rel.market_status == MarketState.OPEN
    assert rel.session().is_open


def test_statistics():
    broker = PaperBroker(seed=3)
    broker.seed_history("RELIANCE", timeframe="5m")
    rel = Equity("RELIANCE", broker=broker)
    rel._history.fetch(timeframe="5m")
    stats = rel.analytics.statistics()
    assert "last" in stats
    assert "total_return_pct" in stats
    assert stats["volatility_pct"] >= 0


def test_statistics_empty():
    rel = Equity("RELIANCE")
    assert rel.analytics.statistics() == {}


def test_candles_ticks_stream_accessors():
    broker = PaperBroker()
    broker.seed_history("RELIANCE", timeframe="5m")
    rel = Equity("RELIANCE", broker=broker)
    rel._history.fetch(timeframe="5m")
    assert len(rel.market.candles()) == 200
    rel.stream.subscribe()
    broker.push_tick(rel, 2500.0)
    assert len(rel.stream.ticks()) == 1
    assert rel._stream is not None


def test_supertrend_behavior():
    broker = PaperBroker()
    broker.seed_history("RELIANCE", timeframe="5m")
    rel = Equity("RELIANCE", broker=broker)
    rel._history.fetch(timeframe="5m")
    signal = rel.analytics.supertrend()
    assert signal in ("up", "down")


def test_detect_imbalance():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    depth = MarketDepth(
        symbol="RELIANCE",
        bids=(DepthLevel(price=99.0, quantity=1000), DepthLevel(price=98.0, quantity=500)),
        asks=(DepthLevel(price=101.0, quantity=250), DepthLevel(price=102.0, quantity=250)),
    )
    rel._depth = depth
    assert rel.analytics.detect_imbalance() == pytest.approx(0.5, abs=0.01)  # (1500-500)/(1500+500)


def test_detect_breakout():
    broker = PaperBroker(seed=5)
    broker.seed_history("RELIANCE", timeframe="5m")
    rel = Equity("RELIANCE", broker=broker)
    df = rel._history.fetch(timeframe="5m").df.copy()
    # Force the last close above everything before it.
    df.loc[df.index[-1], "close"] = df["close"].iloc[:-1].max() * 1.05
    rel._history._df = df
    assert rel.analytics.detect_breakout(lookback=20)


def test_future_cost_of_carry():
    spot = Equity("RELIANCE")
    spot._quote = spot._quote.with_update(ltp=2500.0)
    fut = Future("RELIANCE FUT", underlying="RELIANCE", expiry=date.today() + timedelta(days=30))
    fut.set_underlying(spot)
    fut._quote = fut._quote.with_update(ltp=2510.0)
    carry = fut.cost_of_carry(risk_free=0.065)
    assert carry > 0  # contango
    assert fut.basis() == 10.0


def test_future_rollover_links_underlying():
    spot = Equity("RELIANCE")
    fut = Future("RELIANCE FUT", underlying="RELIANCE", expiry=date.today())
    fut.set_underlying(spot)
    rolled = fut.rollover(date.today() + timedelta(days=30))
    assert rolled.underlying is spot
    assert rolled.expiry == date.today() + timedelta(days=30)


def test_future_continuous():
    broker = PaperBroker()
    broker.seed_history("RELIANCE FUT", timeframe="5m")
    spot = Equity("RELIANCE", broker=broker)
    fut = Future("RELIANCE FUT", underlying="RELIANCE", expiry=date.today() + timedelta(days=30), broker=broker)
    fut.set_underlying(spot)
    fut._history.fetch(timeframe="5m")
    cont = fut.continuous()
    assert not cont.empty
    assert {"open", "high", "low", "close"} <= set(cont.columns)
    assert cont["close"].iloc[0] == pytest.approx(100.0)  # normalised to 100


def test_history_indicators_method():
    broker = PaperBroker()
    broker.seed_history("RELIANCE", timeframe="5m")
    rel = Equity("RELIANCE", broker=broker)
    rel._history.fetch(timeframe="5m")
    bundle = rel._history.indicators()
    assert "rsi_14" in bundle
    assert "vwap" in bundle
