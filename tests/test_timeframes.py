import pytest
from ntrade.domain.timeframes import (
    TIMEFRAME_SECONDS, WIRE_INTERVALS,
    SESSION_BAR_MINUTES,
    timeframe_seconds, interval_bar_minutes,
    interval_span_minutes, interval_span_seconds,
    normalize_interval,
)

def test_timeframe_seconds_intraday():
    assert timeframe_seconds("1m") == 60
    assert timeframe_seconds("5m") == 300
    assert timeframe_seconds("15m") == 900
    assert timeframe_seconds("1h") == 3600
    assert timeframe_seconds("1d") == 86400
    assert timeframe_seconds("1D") == 86400

def test_timeframe_seconds_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported"):
        timeframe_seconds("3m")

def test_interval_bar_minutes_session_day():
    assert interval_bar_minutes("1D") == 375
    assert interval_bar_minutes("1m") == 1
    assert interval_bar_minutes("1h") == 60

def test_interval_span_minutes_calendar_day():
    assert interval_span_minutes("1D") == 1440
    assert interval_span_minutes("1h") == 60

def test_interval_span_seconds():
    assert interval_span_seconds("1D") == 86400
    assert interval_span_seconds("1m") == 60

def test_normalize_interval():
    assert normalize_interval("1d") == "1D"
    assert normalize_interval("1D") == "1D"
    assert normalize_interval("5m") == "5m"

def test_timeframe_seconds_dict_includes_all():
    assert set(TIMEFRAME_SECONDS.keys()) >= {"1m","5m","15m","1h","1d","1D"}
    assert WIRE_INTERVALS == ("1m","5m","15m","1h","1D")
    assert SESSION_BAR_MINUTES == 375
