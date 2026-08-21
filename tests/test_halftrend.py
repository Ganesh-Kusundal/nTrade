"""HalfTrend indicator parity invariants.

These pin the observable behaviour of the domain port to the Pine v6
reference (copyright (c) 2021-present Alex Orekhov (everget), GPL-3.0):

  - ``ht`` equals ``up`` when ``trend == 0`` and ``down`` when ``trend == 1``
  - channels sideline the ht line: ``atrHigh >= ht >= atrLow``
  - ``buySignal`` fires only on a down→up flip (``trend[1] == 1``, ``trend == 0``)
  - ``sellSignal`` fires only on an up→down flip, and the two are never
    simultaneous.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ntrade.domain.analytics.halftrend import halftrend


def _make_frame(n: int = 600, seed: int = 71) -> pd.DataFrame:
    """Deterministic synthetic frame with enough bars to fully warm ATR(100)."""
    rng = np.random.default_rng(seed)
    close = 500.0 + np.cumsum(rng.standard_normal(n) * 2.0)
    spread = np.abs(rng.standard_normal(n) * 0.8)
    high = close + spread
    low = close - spread
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


def test_ht_matches_up_when_trend_zero_and_down_when_trend_one():
    out = _make_frame_tagged()
    for i in range(len(out)):
        ht = out["ht"].iloc[i]
        if not pd.isna(ht):
            if out["trend"].iloc[i] == 0:
                assert ht == pytest.approx(float(out["up"].iloc[i]), rel=1e-6)
            else:
                assert ht == pytest.approx(float(out["down"].iloc[i]), rel=1e-6)


def test_channels_envelop_ht():
    out = _make_frame_tagged()
    valid = out[(out["atrHigh"].notna()) & (out["atrLow"].notna()) & (out["ht"].notna())]
    assert len(valid) > 0
    assert (valid["ht"] <= valid["atrHigh"]).all()
    assert (valid["ht"] >= valid["atrLow"]).all()


def test_buy_signal_only_on_downwards_flip():
    out = _make_frame_tagged()
    for i in range(1, len(out)):
        if out["buySignal"].iloc[i]:
            assert out["trend"].iloc[i] == 0
            assert out["trend"].iloc[i - 1] == 1


def test_sell_signal_only_on_upwards_flip():
    out = _make_frame_tagged()
    for i in range(1, len(out)):
        if out["sellSignal"].iloc[i]:
            assert out["trend"].iloc[i] == 1
            assert out["trend"].iloc[i - 1] == 0


def test_never_both_signals():
    out = _make_frame_tagged()
    assert not (out["buySignal"] & out["sellSignal"]).any()


def _make_frame_tagged():
    out = halftrend(_make_frame())
    # give the frame the same columns shape; reuse index
    return out


def test_overlay_pipeline_no_markers_during_atr_warmup():
    """OverlayPipeline must not emit markers before ATR(100) warms up."""
    from ntrade.analytics.overlay_pipeline import _build_halftrend

    df = _make_frame(n=150, seed=42)
    df["time"] = list(range(len(df)))
    result = _build_halftrend(df, {"amplitude": 2, "channel_deviation": 2, "atr_period": 100})
    assert result is not None
    for m in result["markers"]:
        assert m["index"] >= 100, f"marker at index {m['index']} is during warmup"
