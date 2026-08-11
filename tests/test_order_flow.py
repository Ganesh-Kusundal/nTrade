"""Tests for the order-flow approximation layer (tick-rule CVD + absorption).

TDD: written before the implementation — defines the contract for
``ntrade/domain/analytics/order_flow.py``.

Honest label: Dhan provides no trade tape, so CVD/absorption are
approximations (tick-rule live, OHLCV proxy in backtest) — never true
institutional order flow.
"""

import pandas as pd
import pytest

from ntrade.domain.analytics.order_flow import (
    CvdTracker,
    cvd_from_ohlcv,
    detect_absorptions,
)


# ---------------------------------------------------------------- CvdTracker

def test_cvd_tracker_up_ticks_buy_volume():
    t = CvdTracker()
    assert t.update(100.0, 5) == 0.0      # first tick: no reference, side 0
    assert t.update(100.5, 10) == 10.0    # up tick -> buy
    assert t.delta == 10.0


def test_cvd_tracker_down_ticks_sell_volume():
    t = CvdTracker()
    t.update(100.0, 5)
    t.update(99.5, 8)                     # down tick -> sell
    assert t.delta == -8.0


def test_cvd_tracker_mixed_sequence():
    t = CvdTracker()
    t.update(100.0, 0)
    t.update(100.2, 10)   # +10
    t.update(100.1, 4)    # -4
    t.update(100.3, 7)    # +7
    assert t.delta == pytest.approx(13.0)


def test_cvd_tracker_same_price_carries_side():
    t = CvdTracker()
    t.update(100.0, 0)
    t.update(100.2, 10)   # buy side established
    t.update(100.2, 3)    # same price -> carries buy side
    assert t.delta == pytest.approx(13.0)


def test_cvd_tracker_zero_qty_ignored():
    t = CvdTracker()
    t.update(100.0, 0)
    t.update(100.5, 0)
    t.update(99.0, 0)
    assert t.delta == 0.0


def test_cvd_tracker_quote_tick_does_not_reclassify_side():
    t = CvdTracker()
    t.update(100.0, 5)   # first tick: side 0
    t.update(99.0, 2)    # down -> sell side (-2)
    t.update(100.0, 0)   # quote-only tick: price up but qty 0 -> must NOT
                         # flip the carried side (old bug reclassified it +)
    t.update(100.0, 3)   # unchanged price carries the SELL side -> -3
    assert t.delta == pytest.approx(-5.0)
    assert t.cvd == pytest.approx(-5.0)


# ------------------------------------------------------------ cvd_from_ohlcv

def test_cvd_proxy_uptrend_positive():
    df = pd.DataFrame({
        "open": [100.0] * 10,
        "high": [102.0] * 10, "low": [98.0] * 10,
        "close": [100.5 + i for i in range(10)],
        "volume": [100] * 10,
    })
    cvd = cvd_from_ohlcv(df)
    assert float(cvd.iloc[-1]) > 0
    assert (cvd.diff().dropna() > 0).all()  # monotonic in a pure uptrend


def test_cvd_proxy_downtrend_negative():
    df = pd.DataFrame({
        "open": [100.0] * 10,
        "high": [102.0] * 10, "low": [98.0] * 10,
        "close": [100.5 - i for i in range(10)],
        "volume": [100] * 10,
    })
    assert float(cvd_from_ohlcv(df).iloc[-1]) < 0


def test_cvd_proxy_empty():
    assert cvd_from_ohlcv(pd.DataFrame()).empty


# ---------------------------------------------------------- detect_absorptions

def _bars(ranges, vols, *, closes=None):
    n = len(ranges)
    closes = closes or [100.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "open": [c - 0.05 for c in closes],
        "high": [c + r for c, r in zip(closes, ranges)],
        "low": [c - r for c, r in zip(closes, ranges)],
        "close": closes,
        "volume": vols,
    })


def test_absorption_detected_on_high_volume_compressed_bar():
    # 19 quiet bars, then one huge-volume tiny-range bar -> absorption.
    ranges = [1.0] * 20
    vols = [100] * 19 + [1000]        # 10x average
    ranges[-1] = 0.1                  # compressed
    bars = _bars(ranges, vols)
    abs_list = detect_absorptions(bars, avg_volume_mult=1.5, range_threshold=0.5,
                                  range_size=2.0)
    assert any(a.bar_index == 19 for a in abs_list)


def test_no_absorption_on_normal_bars():
    ranges = [1.0] * 20
    vols = [100] * 20
    bars = _bars(ranges, vols)
    assert detect_absorptions(bars, avg_volume_mult=1.5, range_threshold=0.5,
                              range_size=2.0) == []


def test_high_volume_wide_bar_not_absorption():
    ranges = [1.0] * 20
    vols = [100] * 19 + [1000]
    ranges[-1] = 1.5                   # wide range -> not compressed
    bars = _bars(ranges, vols)
    abs_list = detect_absorptions(bars, avg_volume_mult=1.5, range_threshold=0.5,
                                  range_size=2.0)
    assert all(a.bar_index != 19 for a in abs_list)


def test_absorption_side_from_close_vs_open():
    closes = [100.0 + i * 0.1 for i in range(20)]
    ranges = [1.0] * 20
    vols = [100] * 19 + [800]
    ranges[-1] = 0.1
    # Last close well above its open -> BUY absorption.
    bars = _bars(ranges, vols, closes=closes)
    last = [a for a in detect_absorptions(
        bars, avg_volume_mult=1.5, range_threshold=0.5, range_size=2.0)
        if a.bar_index == 19]
    assert last and last[0].side == "BUY"


def test_absorption_strength_in_zero_one():
    ranges = [1.0] * 20
    vols = [100] * 19 + [300]
    ranges[-1] = 0.1
    bars = _bars(ranges, vols)
    abs_list = detect_absorptions(bars, avg_volume_mult=1.5, range_threshold=0.5,
                                  range_size=2.0)
    for a in abs_list:
        assert 0.0 <= a.strength <= 1.0


def test_absorption_empty_input():
    assert detect_absorptions(pd.DataFrame()) == []
