"""Tests for volume profile construction (POC / VAH / VAL).

TDD: written before the implementation — defines the contract for
``build_volume_profile`` in ``ntrade/domain/analytics/volume_profile.py``.

Contract: each bar's volume is allocated to the price bucket (width ``step``)
containing the bar's midpoint; buckets are centred on multiples of ``step``
(half-up rounding). POC / VAH / VAL are reported as bucket centres.
"""

import pandas as pd
import pytest

from ntrade.domain.analytics.volume_profile import build_volume_profile


def _bars(prices, volumes, step=1.0):
    """One single-price bar per level (midpoint == price)."""
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-08-03 09:15", periods=len(prices), freq="1min"),
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": volumes,
    })


def test_poc_is_max_volume_level():
    prices = [100, 101, 102, 103, 104]
    vols = [100, 400, 250, 150, 50]
    vp = build_volume_profile(_bars(prices, vols), step=1.0)
    assert vp.poc == pytest.approx(101.0)


def test_value_area_captures_68_percent():
    prices = [100, 101, 102, 103, 104]
    vols = [100, 400, 250, 150, 50]
    vp = build_volume_profile(_bars(prices, vols), step=1.0)
    total = sum(vols)
    va_volume = sum(level.volume for level in vp.levels if vp.val <= level.price <= vp.vah)
    assert 0.60 * total <= va_volume <= 0.75 * total


def test_vah_val_order():
    prices = [100, 101, 102, 103, 104]
    vols = [100, 400, 250, 150, 50]
    vp = build_volume_profile(_bars(prices, vols), step=1.0)
    assert vp.val <= vp.poc <= vp.vah
    assert vp.val < vp.vah


def test_empty_input():
    vp = build_volume_profile(pd.DataFrame())
    assert vp.poc == 0.0 and vp.vah == 0.0 and vp.val == 0.0
    assert vp.levels == ()


def test_custom_step_buckets():
    # step=2 -> buckets centred on 100, 102, 104, ...
    prices = [100, 102, 104, 106]
    vols = [100, 100, 500, 100]
    vp = build_volume_profile(_bars(prices, vols), step=2.0)
    assert vp.step == 2.0
    assert vp.poc == pytest.approx(104.0)


def test_levels_reported_as_bucket_centres():
    prices = [100, 101]
    vols = [100, 100]
    vp = build_volume_profile(_bars(prices, vols), step=1.0)
    centres = {level.price for level in vp.levels}
    assert 100.0 in centres and 101.0 in centres


def test_delta_and_total_fields():
    prices = [100, 101]
    vols = [300, 100]
    vp = build_volume_profile(_bars(prices, vols), step=1.0)
    level = next(level for level in vp.levels if level.price == 100)
    assert level.volume == pytest.approx(300.0)
    assert level.total == pytest.approx(300.0)
    assert level.delta == pytest.approx(0.0)  # no buy/sell split supplied


def test_delta_positive_when_buy_volume_supplied():
    # With an explicit buy/sell split, delta = buy - (volume - buy).
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-03 09:15", periods=2, freq="1min"),
        "open": [100.0, 101.0], "high": [101.0, 102.0],
        "low": [99.0, 100.0], "close": [100.5, 101.5],
        "volume": [200.0, 200.0],
    })
    vp = build_volume_profile(df, step=1.0, buy_volume=[150.0, 50.0])
    level = next(level for level in vp.levels if level.price == 100)
    assert level.delta == pytest.approx(100.0)  # 150 buy - 50 sell
    assert level.total == pytest.approx(200.0)


def test_flat_profile_poc_single_level():
    prices = [100] * 5
    vols = [100] * 5
    vp = build_volume_profile(_bars(prices, vols), step=1.0)
    assert vp.poc == pytest.approx(100.0)


def test_buy_volume_length_mismatch_raises():
    df = _bars([100, 101], [100, 100])
    with pytest.raises(ValueError, match="buy_volume"):
        build_volume_profile(df, step=1.0, buy_volume=[150.0])
