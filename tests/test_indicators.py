"""Tests for indicators (RSI, ATR, VWAP, Supertrend)."""

import pandas as pd
import pytest

from ntrade.domain.analytics.indicators import atr, compute_bundle, ema, rsi, sma, supertrend, vwap


@pytest.fixture
def df():
    # Simple uptrend: every candle closes higher.
    closes = [100 + i for i in range(50)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=50, freq="5min"),
        "open": closes,
        "high": [c + 2 for c in closes],
        "low": [c - 2 for c in closes],
        "close": closes,
        "volume": [1000] * 50,
    })


def test_rsi_in_bounds(df):
    r = rsi(df)
    assert len(r) == 50
    assert r.dropna().between(0, 100).all()


def test_rsi_trend_direction(df):
    r = rsi(df, period=14)
    # Strong uptrend -> RSI near/above 70 territory on the last value.
    assert r.iloc[-1] > 50


def test_atr_positive(df):
    a = atr(df)
    assert a.iloc[-1] > 0


def test_vwap_within_full_range(df):
    v = vwap(df)
    # VWAP is a cumulative average — it must stay within the whole session's range.
    assert df["low"].min() <= v.iloc[-1] <= df["high"].max()


def test_supertrend_up_on_uptrend(df):
    st = supertrend(df, atr_period=10, multiplier=3)
    col = "STX_10_3"
    assert col in st.columns
    assert st[col].iloc[-1] in ("up", "down")
    # In a clean uptrend the final signal should be "up".
    assert st[col].iloc[-1] == "up"


def test_supertrend_down_on_downtrend():
    closes = [100 - i for i in range(50)]
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=50, freq="5min"),
        "open": closes, "high": [c + 2 for c in closes],
        "low": [c - 2 for c in closes], "close": closes,
        "volume": [1000] * 50,
    })
    st = supertrend(df, atr_period=10, multiplier=3)
    assert st["STX_10_3"].iloc[-1] == "down"


def test_compute_bundle(df):
    bundle = compute_bundle(df)
    assert "rsi_14" in bundle
    assert "atr_14" in bundle
    assert "vwap" in bundle
    assert "stx_10_3" in bundle
    assert "avg_volume" in bundle
    assert bundle["avg_volume"] == 1000.0


def test_sma():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=10, freq="5min"),
        "open": [100] * 10, "high": [102] * 10, "low": [98] * 10,
        "close": list(range(100, 110)), "volume": [1000] * 10,
    })
    s = sma(df, period=5)
    assert len(s) == 10
    # the 5-candle SMA at index 5 = mean(closes[1:6])
    assert s.iloc[5] == sum(range(101, 106)) / 5
    assert pd.isna(s.iloc[3])  # warm-up period is NaN


def test_ema_follows_trend():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [100] * 30, "high": [102] * 30, "low": [98] * 30,
        "close": [100 + i for i in range(30)], "volume": [1000] * 30,
    })
    e = ema(df, period=9)
    assert len(e) == 30
    # uptrend -> EMA sits below the latest close but above the earlier ones
    assert e.iloc[-1] < df["close"].iloc[-1]
    assert e.iloc[-1] > 100
    assert pd.isna(e.iloc[7])  # warm-up (9 candles) is NaN


def test_ema_bundle_default_periods():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=30, freq="5min"),
        "open": [100] * 30, "high": [102] * 30, "low": [98] * 30,
        "close": [100 + i for i in range(30)], "volume": [1000] * 30,
    })
    bundle = compute_bundle(df)
    assert "ema_9" in bundle
    assert "ema_21" in bundle
    assert "ema_9" in bundle and "ema_21" in bundle


def test_ema_bundle_custom_periods():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=50, freq="5min"),
        "open": [100] * 50, "high": [102] * 50, "low": [98] * 50,
        "close": [100 + i for i in range(50)], "volume": [1000] * 50,
    })
    bundle = compute_bundle(df, ema_periods=(9, 21), sma_periods=(20,))
    assert "ema_9" in bundle and "ema_21" in bundle
    assert "sma_20" in bundle


def test_compute_bundle_empty():
    assert compute_bundle(pd.DataFrame()) == {}


def test_indicator_engine_bounds_rows():
    from ntrade.engines.indicator_engine import IndicatorEngine
    from ntrade.events.market import CandleClosedEvent
    from ntrade.kernel.event_bus import EventBus
    from datetime import datetime
    from unittest.mock import MagicMock

    bus = EventBus()
    ctx = MagicMock()
    ctx.bus = bus
    ctx.instrument.return_value = None
    engine = IndicatorEngine(ctx, timeframe="1m", max_rows=100)
    for i in range(200):
        event = CandleClosedEvent(
            symbol="X", exchange="NSE", timeframe="1m",
            open=100.0, high=101.0, low=99.0, close=100.5, volume=1000,
            ts=datetime(2026, 1, 1, 9, i % 60, 0),
        )
        engine.on_candle_closed(event)
    assert len(engine._rows["X"]) <= 100
