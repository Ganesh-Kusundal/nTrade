"""Tests for CandleSeries domain type."""

import pandas as pd
import pytest

from ntrade.domain.market.candles import CandleSeries


def _sample_df(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-08-01 09:15", periods=rows, freq="5min"),
        "open": [100.0 + i for i in range(rows)],
        "high": [102.0 + i for i in range(rows)],
        "low": [99.0 + i for i in range(rows)],
        "close": [101.0 + i for i in range(rows)],
        "volume": [1000 + i * 1000 for i in range(rows)],
    })


def test_creation():
    df = _sample_df()
    cs = CandleSeries(df, symbol="RELIANCE", timeframe="5m")
    assert cs.symbol == "RELIANCE"
    assert cs.timeframe == "5m"
    assert len(cs) == 3


def test_to_dataframe():
    df = _sample_df()
    cs = CandleSeries(df, symbol="RELIANCE", timeframe="5m")
    result = cs.to_dataframe()
    assert isinstance(result, pd.DataFrame)
    assert result is df
    assert list(result.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


def test_len():
    assert len(CandleSeries(_sample_df(5), symbol="X", timeframe="1m")) == 5
    assert len(CandleSeries(pd.DataFrame(), symbol="X", timeframe="1m")) == 0


def test_empty():
    assert CandleSeries(pd.DataFrame(), symbol="X", timeframe="1m").empty is True
    assert CandleSeries(_sample_df(), symbol="X", timeframe="1m").empty is False


def test_repr():
    cs = CandleSeries(_sample_df(3), symbol="NIFTY", timeframe="15m")
    r = repr(cs)
    assert "CandleSeries" in r
    assert "NIFTY" in r
    assert "15m" in r
    assert "rows=3" in r


def test_symbol_and_timeframe_properties():
    cs = CandleSeries(pd.DataFrame(), symbol="GOLD", timeframe="1d")
    assert cs.symbol == "GOLD"
    assert cs.timeframe == "1d"


def test_default_symbol_and_timeframe():
    cs = CandleSeries(pd.DataFrame())
    assert cs.symbol == ""
    assert cs.timeframe == ""


def test_getattr_delegation():
    df = _sample_df()
    cs = CandleSeries(df, symbol="RELIANCE", timeframe="5m")
    # columns delegation
    assert list(cs.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    # iloc delegation
    assert cs.iloc[0]["open"] == 100.0
    # head delegation
    assert len(cs.head(2)) == 2


def test_getattr_raises_for_unknown():
    cs = CandleSeries(_sample_df(), symbol="X", timeframe="1m")
    with pytest.raises(AttributeError, match="CandleSeries has no attribute"):
        cs.nonexistent_thing


def test_backward_compat_with_dataframe_usage():
    """CandleSeries must work where a DataFrame was previously returned."""
    df = _sample_df()
    cs = CandleSeries(df, symbol="RELIANCE", timeframe="5m")
    # len() works
    assert len(cs) == 3
    # .columns works
    assert {"timestamp", "open", "high", "low", "close", "volume"} <= set(cs.columns)
    # .empty works
    assert cs.empty is False
    # iteration works (via __getattr__ -> _df)
    for col in cs.columns:
        assert col in df.columns
