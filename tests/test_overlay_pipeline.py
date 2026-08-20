"""OverlayPipeline parity tests — backend is the single source of truth.

These pin the overlay math (VWAP, volume profile POC/VAH/VAL, absorptions)
to the same pure modules the live/backtest engines use, and prove the
strategy overlay is produced by replaying the *real* strategy classes (no
fork). Together they are the golden contract the frontend must match before
its TypeScript calc mirrors are deleted (plan M6).
"""

from __future__ import annotations

import random

import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from ntrade.analytics.overlay_pipeline import (
    _candles_to_frame, _session_anchor, build_overlays, replay_strategy)
from ntrade.domain.analytics.indicators import vwap, vwap_bands
from ntrade.domain.analytics.order_flow import detect_absorptions
from ntrade.domain.analytics.volume_profile import build_volume_profile
from ntrade.engines.strategies import _strategy_classes
from ntrade.events.market import CandleClosedEvent

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


def test_absorptions_detected_and_match_pure_module():
    candles = _make_session_candles()
    df = _candles_to_frame(candles)
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO", interval="1m")

    expected = detect_absorptions(df)
    assert dto.absorptions is not None
    idxs = {a["index"] for a in dto.absorptions}
    assert 120 in idxs
    assert len(dto.absorptions) == len(expected)


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


@pytest.mark.parametrize("strategy_id", ["valentini", "morning_vah_val", "ema_cross"])
def test_strategy_replay_uses_real_classes(strategy_id):
    candles = _make_session_candles(n=400, seed=5)
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO",
                         interval="1m", strategy_id=strategy_id)
    assert dto.strategy is not None
    assert dto.strategy["id"] == strategy_id
    assert dto.strategy["id"] in _strategy_classes
    for sig in dto.strategy["signals"]:
        assert sig["side"] in ("BUY", "SELL")
        assert sig["quantity"] > 0


def test_strategy_snapshot_exposes_profile_levels():
    candles = _make_session_candles(n=400, seed=7)
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO",
                         interval="1m", strategy_id="valentini")
    assert dto.strategy is not None
    assert dto.strategy.get("levels")
    lvl = dto.strategy["levels"][0]
    assert {"vah", "val", "poc"} <= set(lvl)


def test_no_strategy_returns_null_section():
    candles = _make_session_candles()
    dto = build_overlays(candles, symbol="BANKNIFTY", exchange="NFO", interval="1m")
    assert dto.strategy is None
    assert dto.volume_profile is not None


def test_empty_candles_safe():
    dto = build_overlays([], symbol="X", exchange="NFO", interval="1m",
                         strategy_id="valentini")
    assert dto.vwap is None
    assert dto.volume_profile is None
    assert dto.absorptions is None
    assert dto.strategy is not None


def test_replay_matches_direct_strategy_drive():
    """Golden: replay_strategy must be bit-identical to feeding the kernel's
    real CandleClosedEvent path — otherwise the chart would diverge from
    paper/live fills (the zero-parity rule)."""
    from ntrade.analytics.overlay_pipeline import (
        _FlatPortfolio, _IndicatorStub, _SignalBus)

    candles = _make_session_candles(n=400, seed=9)
    strategy_id = "valentini"
    cls = _strategy_classes[strategy_id]

    # --- path A: the public headless adapter ---
    via_adapter = replay_strategy(
        candles, strategy_id=strategy_id, symbol="BANKNIFTY",
        exchange="NFO", timeframe="1m")
    adapter_signals = [(s["side"], s["quantity"]) for s in via_adapter["signals"]]

    # --- path B: drive the real class directly via its hooks (kernel path) ---
    captured = []
    inst = cls()
    ctx = type("_C", (), {})()
    ctx.mode = "replay"
    ctx._ts = None
    ctx.now = lambda: ctx._ts
    ctx.bus = _SignalBus(captured.append)
    ctx.account = type("_A", (), {"balance": 100_000.0})()
    ctx.portfolio = _FlatPortfolio()
    ctx.instrument = lambda sym: _IndicatorStub([])
    inst.ctx = ctx
    for c in candles:
        ts = pd.Timestamp(c["time"], unit="s", tz=_IST)
        ctx._ts = ts
        inst.on_candle_closed(CandleClosedEvent(
            symbol="BANKNIFTY", exchange="NFO", timeframe="1m",
            open=float(c["open"]), high=float(c["high"]), low=float(c["low"]),
            close=float(c["close"]), volume=float(c["volume"]), ts=ts))

    direct_signals = [(s.side, s.quantity) for s in captured]
    # Same number of signals, same (side, qty) sequence.
    assert len(adapter_signals) == len(direct_signals)
    assert adapter_signals == direct_signals
    # Same snapshot (phase + level POC/VAH/VAL).
    assert via_adapter["phase"] == inst.phase
    if via_adapter.get("levels"):
        assert round(float(inst._profile.poc), 4) == via_adapter["levels"][0]["poc"]
        assert round(float(inst._profile.vah), 4) == via_adapter["levels"][0]["vah"]
        assert round(float(inst._profile.val), 4) == via_adapter["levels"][0]["val"]
