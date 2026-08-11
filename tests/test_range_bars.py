"""Tests for the range bar generator (price-based bars from 1m OHLCV).

TDD: written before the implementation — these define the contract for
``build_range_bars`` / ``calc_auto_range`` in
``ntrade/domain/analytics/range_bars.py``.
"""

import pandas as pd
import pytest

from ntrade.domain.analytics.range_bars import build_range_bars, calc_auto_range


def _frame(closes, *, step=5.0, vol=1000, start="2026-08-03 09:15"):
    """Synthetic 1m frame: each candle spans [close-step, close+step]."""
    n = len(closes)
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq="1min"),
        "open": closes,
        "high": [c + step for c in closes],
        "low": [c - step for c in closes],
        "close": closes,
        "volume": [vol] * n,
    })


def test_calc_auto_range_positive():
    df = _frame([100 + i for i in range(30)])
    r = calc_auto_range(df)
    assert r > 0


def test_calc_auto_range_scales_with_atr():
    calm = _frame([100.0] * 30, step=1.0)
    wild = _frame([100.0] * 30, step=10.0)
    assert calc_auto_range(wild) > calc_auto_range(calm)


def test_calc_auto_range_respects_tick_grid():
    df = _frame([100 + i for i in range(30)])
    r = calc_auto_range(df, tick_size=0.05)
    # rounded to the tick grid: a multiple of 0.05
    assert abs(r / 0.05 - round(r / 0.05)) < 1e-9


def test_empty_input_returns_empty():
    out = build_range_bars(pd.DataFrame())
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_every_complete_bar_spans_range_size():
    # Uptrend of 2.5/bar with range_size=10 -> each complete bar covers >= 10.
    closes = [100 + i * 2.5 for i in range(40)]
    df = _frame(closes, step=1.0)
    bars = build_range_bars(df, range_size=10.0)
    complete = bars[bars["is_complete"]]
    assert len(complete) >= 3
    assert (complete["high"] - complete["low"] >= 10.0).all()


def test_last_bar_is_incomplete():
    # 43 candles, range 10 -> 10 full bars + a trailing partial bar.
    closes = [100 + i * 2.0 for i in range(43)]
    df = _frame(closes, step=1.0)
    bars = build_range_bars(df, range_size=10.0)
    assert len(bars[bars["is_complete"]]) == 10
    assert not bars.iloc[-1]["is_complete"]


def test_volume_conserved_approximately():
    closes = [100 + i * 1.5 for i in range(50)]
    df = _frame(closes, step=1.0, vol=1000)
    bars = build_range_bars(df, range_size=8.0)
    total_bar_vol = bars["volume"].sum()
    total_candle_vol = df["volume"].sum()
    # Volume is distributed proportionally to the path segments each bar
    # consumed, so totals match to within a small tolerance.
    assert abs(total_bar_vol - total_candle_vol) < total_candle_vol * 0.01


def test_monotonic_trend_bar_count():
    # 2.0/candle, range 10 -> the path simulation yields 7 full bars in 30
    # candles (verified empirically; bars close mid-candle on the H->L sweep).
    closes = [100 + i * 2.0 for i in range(30)]
    df = _frame(closes, step=1.0)
    bars = build_range_bars(df, range_size=10.0)
    complete = bars[bars["is_complete"]]
    assert len(complete) == 7


def test_explicit_range_size_beats_auto():
    # Flat frame spans 4.0/candle: auto (ATR=4) closes ~1 bar/candle; a
    # tiny explicit 0.5 range closes several bars per candle.
    df = _frame([100.0] * 20, step=2.0)
    auto = build_range_bars(df)                    # auto from ATR
    fixed = build_range_bars(df, range_size=0.5)   # explicit small range
    assert len(fixed) > len(auto)


def test_nan_volume_treated_as_zero():
    # A NaN-volume candle must not poison bar volumes (NaN is truthy, so the
    # old `row.get("volume", 0) or 0` propagated it into every bar).
    closes = [100 + i * 2.0 for i in range(20)]
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-03 09:15", periods=20, freq="1min"),
        "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
        "volume": [float("nan") if i == 5 else 100.0 for i in range(20)],
    })
    bars = build_range_bars(df, range_size=10.0)
    assert bars["volume"].sum() == pytest.approx(df["volume"].sum())
    assert bars["volume"].notna().all()


def test_exact_final_completion_marks_all_complete():
    # A candle whose first path segment spans exactly range_size closes a bar
    # immediately; with no leftover partial bar, every bar must be complete
    # (regression: the last bar used to be unconditionally labeled partial).
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-03 09:15", periods=1, freq="1min"),
        "open": [100.0], "high": [110.0], "low": [90.0],
        "close": [105.0], "volume": [1000],
    })
    bars = build_range_bars(df, range_size=10.0)
    assert not bars.empty
    assert bars["is_complete"].all()


def test_columns_present():
    closes = [100 + i for i in range(20)]
    bars = build_range_bars(_frame(closes), range_size=10.0)
    for col in ("timestamp", "open", "high", "low", "close", "volume", "is_complete"):
        assert col in bars.columns
