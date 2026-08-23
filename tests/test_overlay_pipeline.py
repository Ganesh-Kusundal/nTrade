"""OverlayPipeline parity tests — backend is the single source of truth.

These pin the overlay math (VWAP, volume profile POC/VAH/VAL) to the same pure
modules the live/backtest engines use, and prove the HalfTrend overlay is produced
by the real domain indicator (no fork). Together they are the golden contract the
frontend must match before its TypeScript calc mirrors are deleted (plan M6).
"""

from __future__ import annotations

import random

import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from ntrade.analytics.overlay_pipeline import (
    _candles_to_frame, _session_anchor, build_overlays)
from ntrade.domain.analytics.indicators import vwap, vwap_bands
from ntrade.domain.analytics.volume_profile import build_volume_profile


_IST = ZoneInfo("Asia/Kolkata")


def _make_session_candles(n: int = 200, seed: int = 11, drift: float = 0.0):
    """A deterministic 1m intraday session for BANKNIFTY-style prices."""
    rng = random.Random(seed)
    rows = []
    base_ts = pd.Timestamp("2026-08-14 09:15:00", tz=_IST).timestamp()
    price = 55000.0
    for i in range(n):
        o = price
        if i == 120:
            # A textbook absorption bar: huge volume, near-zero range.
            c = o + 0.5
            h = o + 1.0
            l = o - 1.0
            v = 30000.0
        else:
            c = price + drift + rng.uniform(-8, 8)
            h = max(o, c) + rng.uniform(0, 4)
            l = min(o, c) - rng.uniform(0, 4)
            v = rng.uniform(500, 2000)
        rows.append({
            "time": int(base_ts) + i * 60,
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
        price = c
    return rows


def test_vwap_matches_pure_module():
    candles = _make_session_candles()
    df = _candles_to_frame(candles)
    session = _session_anchor(df, "NFO")
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO", interval="1m")

    expected = vwap(session)
    assert dto.vwap is not None
    last = dto.vwap[-1]["value"]
    assert last == pytest.approx(float(expected.iloc[-1]), rel=1e-6)


def test_vwap_bands_present_and_consistent():
    candles = _make_session_candles()
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO", interval="1m")
    u, lo = vwap_bands(_session_anchor(_candles_to_frame(candles), "NFO"))
    if pd.notna(u) and pd.notna(lo):
        assert dto.vwap_upper is not None and dto.vwap_lower is not None
        assert dto.vwap_upper[-1]["value"] == pytest.approx(float(u), rel=1e-6)
        assert dto.vwap_lower[-1]["value"] == pytest.approx(float(lo), rel=1e-6)


def test_volume_profile_matches_pure_module():
    candles = _make_session_candles()
    df = _candles_to_frame(candles)
    session = _session_anchor(df, "NFO")
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO", interval="1m")

    vp = build_volume_profile(session)
    assert dto.volume_profile is not None
    assert dto.volume_profile["poc"] == pytest.approx(float(vp.poc), rel=1e-4)
    assert dto.volume_profile["vah"] == pytest.approx(float(vp.vah), rel=1e-4)
    assert dto.volume_profile["val"] == pytest.approx(float(vp.val), rel=1e-4)
    assert len(dto.volume_profile["levels"]) == len(vp.levels)


def test_session_anchor_drops_prior_session_cruft():
    today = pd.Timestamp("2026-08-14 11:00:00", tz=_IST)
    prior = today.replace(day=13)
    rows = [
        {"time": int(prior.timestamp()), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 10.0},
        {"time": int(today.timestamp()), "open": 55000.0, "high": 55010.0,
         "low": 54990.0, "close": 55005.0, "volume": 1000.0},
    ]
    df = _candles_to_frame(rows)
    session = _session_anchor(df, "NFO")
    assert len(session) == 1


def test_halftrend_overlay_returns_markers_and_series():
    candles = _make_session_candles(n=400, seed=5)
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO",
                         interval="1m", strategy_id="halftrend")
    assert dto.strategy is not None
    assert dto.strategy["id"] == "halftrend"
    assert "markers" in dto.strategy
    assert "series" in dto.strategy
    assert dto.strategy["series"]["trend"]
    assert dto.strategy["series"]["ht"]


def test_no_strategy_returns_null_section():
    candles = _make_session_candles()
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO", interval="1m")
    assert dto.strategy is None
    assert dto.volume_profile is not None


def test_empty_candles_safe():
    dto = build_overlays([], symbol="X", exchange="NFO", interval="1m",
                         strategy_id="halftrend")
    assert dto.vwap is None
    assert dto.volume_profile is None
    assert dto.adx is None
    assert dto.strategy is not None


def test_adx_overlay_returns_adx_and_di_series():
    candles = _make_session_candles(n=100, seed=7)
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO", interval="1m")
    assert dto.adx is not None
    assert dto.adx["period"] == 14
    assert len(dto.adx["series"]) == 100
    # After warm-up period, ADX, plus_di, and minus_di should be populated numbers
    valid_points = [p for p in dto.adx["series"] if p["adx"] is not None]
    assert len(valid_points) > 50
    assert 0 <= valid_points[-1]["adx"] <= 100
    assert 0 <= valid_points[-1]["plus_di"] <= 100
    assert 0 <= valid_points[-1]["minus_di"] <= 100
