"""ValentiniScalper AMT-correction tests (leg anchor / ATR step / volume accumulation).

Self-contained: redefines its own kernel/candle helpers so it does not import
from tests/test_valentini_strategy.py (which carries unrelated uncommitted WIP).
"""

from datetime import datetime, timedelta

import pandas as pd

from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategies import ValentiniScalper
from ntrade.events.market import CandleClosedEvent, QuoteEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel

_TS = datetime(2026, 8, 3, 10, 0)   # within session 09:15-15:25
_NIFTY = "NIFTY"


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=1_000_000.0)
    k.register(Equity(_NIFTY))
    return k


def _candle(k, i, *, close=None, open_=None, high=None, low=None, volume=100,
            ts=None):
    """Publish one 1m closed candle; OHLC default to a small bullish bar."""
    c = close if close is not None else 100.0 + i * 0.5
    o = open_ if open_ is not None else c - 0.5
    h = high if high is not None else max(c, o) + 0.5
    lo = low if low is not None else min(c, o) - 0.5
    ts = ts or (_TS + timedelta(minutes=i))
    k.bus.publish(QuoteEvent(
        symbol=_NIFTY, exchange="NSE", ltp=c, bid=0.0, ask=0.0,
        open=o, high=h, low=lo, volume=volume, ts=ts,
    ))
    k.bus.publish(CandleClosedEvent(
        symbol=_NIFTY, exchange="NSE", timeframe="1m",
        open=o, high=h, low=lo, close=c, volume=volume, ts=ts,
    ))


def _uptrend_bars(k, n=40, start=100.0, step=0.5, volume=100):
    """Publish n rising candles to build warmup + range bars + profile."""
    for i in range(n):
        _candle(k, i, close=start + i * step, volume=volume)


def _absorption_bar(k, i, at, volume=1500, span=0.05, ts=None):
    """Publish a high-volume compressed candle at price ``at`` (absorption)."""
    _candle(k, i, close=at, open_=at - 0.02, high=at + span,
            low=at - span, volume=volume, ts=ts)


def _fixed_profile(monkeypatch, val, poc, vah, step=4.0):
    """Force the strategy's volume-profile analysis to a known POC/VAH/VAL
    (white-box) so the Triple-A location/SL/TP/balance rules are tested in
    isolation from profile construction."""
    from ntrade.domain.analytics.volume_profile import VolumeProfile, VPLevel
    prof = VolumeProfile(
        levels=tuple(
            VPLevel(price=p, volume=1.0) for p in (val, poc, vah)
        ),
        poc=poc, vah=vah, val=val, step=step,
    )
    monkeypatch.setattr(
        "ntrade.engines.strategies.build_volume_profile",
        lambda *a, **kw: prof,
    )
    return prof


# ------------------------------------------------------------------ step (ATR floor)

def test_step_floored_to_atr_when_range_below_atr():
    k = _kernel()
    # Explicit tiny range_size (1.0) with real bars whose ATR(14) is larger:
    # the stop/step must be floored to the ATR, not left at the tight 1.0.
    strat = ValentiniScalper(symbol=_NIFTY, range_size=1.0, warmup=15)
    k.register_strategy(strat)
    # Wide, volatile bars -> ATR(14) well above 1.0.
    for i in range(30):
        _candle(k, i, close=100.0 + i * 0.5, open_=99.0 + i * 0.5,
                high=103.0 + i * 0.5, low=96.0 + i * 0.5, volume=500)
    assert strat._atr > 1.0, f"setup should yield ATR > 1.0, got {strat._atr}"
    assert strat._step >= strat._atr, (
        f"step {strat._step} must be >= ATR {strat._atr}")


# ------------------------------------------------------------------ leg-anchored profile

def _impulse_bar(k, i, at, span=10.0, volume=2000):
    """A single 1m candle whose high-low range >= leg_impulse_mult * range_size
    (2.0 * 4.0 = 8.0) — a directional impulse that starts a new leg."""
    _candle(k, i, close=at, open_=at - span / 2,
            high=at + span / 2, low=at - span / 2, volume=volume)


def test_impulse_candle_reanchors_leg():
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _uptrend_bars(k, 30)                 # leg starts at row 0 (no impulse yet)
    assert strat._leg_start_idx == 0
    _impulse_bar(k, 30, at=115.0)        # span 10 >= 8 -> new leg at row 30
    assert strat._leg_start_idx == 30
    _candle(k, 31, close=116.0)          # a quiet bar keeps the same leg
    assert strat._leg_start_idx == 30
    _impulse_bar(k, 32, at=118.0)        # another impulse -> leg re-anchors
    assert strat._leg_start_idx == 32


def test_impulse_reanchors_leg_when_atr_floor_binds():
    k = _kernel()
    # Tiny range_size (1.0) + wide volatile bars (high-low 5.0) -> ATR(14) of
    # ~5.0 floors the step, so the impulse threshold is leg_impulse_mult * ATR
    # (~10.0), not leg_impulse_mult * range_size (2.0).
    strat = ValentiniScalper(symbol=_NIFTY, range_size=1.0, warmup=15)
    k.register_strategy(strat)
    for i in range(30):
        _candle(k, i, close=100.0 + i * 0.5, open_=99.0 + i * 0.5,
                high=102.5 + i * 0.5, low=97.5 + i * 0.5, volume=500)
    assert strat._leg_start_idx == 0
    assert strat._atr > 1.0, f"setup should yield ATR > 1.0, got {strat._atr}"
    assert strat._step > strat.range_size, (
        f"step {strat._step} must exceed range_size {strat.range_size} "
        "for the ATR floor to be binding")
    _impulse_bar(k, 30, at=115.0, span=14.0)   # span 14 >= 2.0*~5.0
    assert strat._leg_start_idx == 30


# ------------------------------------------------------------------ volume accumulation

def test_accumulation_requires_recent_volume(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)                       # base 100-volume bars
    _absorption_bar(k, 30, at=110.0, volume=1500)  # absorbing at VAL
    assert strat.phase == "absorbing"
    # Bars 31,32 drift back to the POC on LOW volume (well below 1.5x the
    # prior median 100): accumulation must NOT confirm on a dead retrace.
    _candle(k, 31, close=118.0, volume=30)
    _candle(k, 32, close=118.0, volume=30)
    assert strat.phase == "absorbing"
    # A high-volume test of the POC confirms the move -> accumulating.
    _candle(k, 33, close=118.0, volume=300)
    assert strat.phase == "accumulating"


def _fills(k):
    from ntrade.events.order import OrderFilledEvent
    return [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]


def _signals(k):
    from ntrade.events.risk import SignalGeneratedEvent
    return [e for e in k.bus.history if isinstance(e, SignalGeneratedEvent)]


# ------------------------------------------------------------------ swing bias

def test_swing_bias_bullish_on_higher_high_and_low():
    from ntrade.domain.analytics.range_bars import swing_bias
    bars = pd.DataFrame({
        "high": [100.0, 101.0], "low": [99.0, 100.0],
        "close": [100.5, 100.8], "is_complete": [True, True],
    })
    assert swing_bias(bars) == "BUY"


def test_swing_bias_bearish_on_lower_high_and_low():
    from ntrade.domain.analytics.range_bars import swing_bias
    bars = pd.DataFrame({
        "high": [101.0, 100.0], "low": [100.0, 99.0],
        "close": [100.8, 99.8], "is_complete": [True, True],
    })
    assert swing_bias(bars) == "SELL"


def test_swing_bias_indeterminate():
    from ntrade.domain.analytics.range_bars import swing_bias
    # fewer than two completed bars -> no vote
    bars = pd.DataFrame({"high": [100.0], "low": [99.0],
                         "close": [99.5], "is_complete": [True]})
    assert swing_bias(bars) is None
    # incomplete latest bar is excluded -> only one completed -> None
    bars2 = pd.DataFrame({"high": [100.0, 102.0], "low": [99.0, 101.0],
                          "close": [99.5, 101.5],
                          "is_complete": [True, False]})
    assert swing_bias(bars2) is None


# ------------------------------------------------------------------ direction gate

def test_direction_bullish_when_structure_volume_vwap_agree(monkeypatch):
    from ntrade.domain.analytics.range_bars import swing_bias
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: "BUY")
    _uptrend_bars(k, 30)
    strat._vwap = 110.0
    assert strat._direction(close=122.0) == "BUY"


def test_direction_none_when_volume_does_not_support():
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             direction_volume_mult=100.0)
    k.register_strategy(strat)
    _uptrend_bars(k, 30, volume=100)          # 30 bars of vol 100
    # White-box: anchor the leg at bar 20 so there IS prior history for the
    # volume gate to compare against (without an impulse leg, _leg_start_idx
    # stays 0, prior is empty, and _volume_supports short-circuits True).
    strat._leg_start_idx = 20
    strat._vwap = 110.0
    assert strat._direction(close=122.0) is None


def test_direction_none_when_structure_vwap_disagree(monkeypatch):
    from ntrade.domain.analytics.range_bars import swing_bias
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: "SELL")
    _uptrend_bars(k, 30)
    strat._vwap = 110.0
    # structure says SELL, VWAP says BUY -> no trade
    assert strat._direction(close=122.0) is None


def test_direction_falls_back_to_vwap_without_structure_vote(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: None)
    _uptrend_bars(k, 30)
    strat._vwap = 110.0
    assert strat._direction(close=122.0) == "BUY"
    assert strat._direction(close=105.0) == "SELL"


# ------------------------------------------------------------------ direction-gated trigger

def test_trigger_requires_absorption_side_matches_direction(monkeypatch):
    from ntrade.domain.analytics.range_bars import swing_bias
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    # Direction is SELL (structure) but the absorption is BUY at VAL -> the
    # continuation trigger must NOT fire even though every other gate passes.
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: "SELL")
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)          # BUY absorption at VAL
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)               # above VWAP, not in balance
    assert not any(f.side == "BUY" for f in _fills(k))


# ------------------------------------------------------------------ auction trail

def _install_range_bars(monkeypatch, bars_df):
    """Force build_range_bars to return a controlled completed-bar series.
    NOTE: on_candle_closed REBUILDS self._range_bars every candle
    (strategies.py:295-297), so a direct `strat._range_bars = ...` override
    would be wiped before _manage_exit runs. Monkeypatching the builder (the
    _fixed_profile idiom) survives the rebuild."""
    monkeypatch.setattr("ntrade.engines.strategies.build_range_bars",
                        lambda *a, **kw: bars_df)


def _runner_long(k, monkeypatch):
    """Drive the strategy to a filled BUY entry (runner: tp=None) at bar 32."""
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # BUY entry at 122
    buys = [f for f in _fills(k) if f.side == "BUY"]
    assert buys, "entry did not fill"
    return strat, buys[0]


def test_runner_has_no_fixed_target(monkeypatch):
    k = _kernel()
    strat, _ = _runner_long(k, monkeypatch)
    sig = [s for s in _signals(k)
           if s.side == "BUY" and s.metadata.get("phase") == "signal"]
    assert sig
    assert sig[0].metadata.get("tp") is None       # runner, no hard target
    assert sig[0].metadata.get("target") == "runner"


def test_structure_break_exit(monkeypatch):
    k = _kernel()
    strat, _ = _runner_long(k, monkeypatch)
    # Controlled series: latest completed bar closes (118.5) through the prior
    # bar's low (121.0) = structure break. Volume is HIGH (4000) so the
    # divergence gate does NOT fire first (it needs weak volume).
    _install_range_bars(monkeypatch, pd.DataFrame({
        "high": [116.0, 120.0, 124.0, 127.0],
        "low":  [114.0, 117.0, 121.0, 119.0],
        "close":[115.5, 119.0, 123.0, 118.5],
        "volume":[100.0, 100.0, 100.0, 4000.0],
        "is_complete":[True, True, True, True],
    }))
    _candle(k, 33, close=118.5, open_=122.0, high=122.5, low=118.0, volume=100)
    sells = [f for f in _fills(k) if f.side == "SELL"]
    assert sells, "structure break must exit the long"
    reason = [s.metadata.get("exit_reason") for s in _signals(k)
              if s.side == "SELL"]
    assert "structure_break" in reason, f"got reasons {reason}"


def test_volume_divergence_exit(monkeypatch):
    k = _kernel()
    strat, _ = _runner_long(k, monkeypatch)
    # Controlled series ends with a HIGHER high (127) on weak volume (100);
    # the same bar's close (126) is NOT below the prior low (121) so the
    # structure-break gate does not fire. Impulse volume at entry ~4700 so
    # 0.6 x 4700 = 2820 > 100 -> divergence fires.
    _install_range_bars(monkeypatch, pd.DataFrame({
        "high": [116.0, 120.0, 124.0, 127.0],
        "low":  [114.0, 117.0, 121.0, 122.0],
        "close":[115.5, 119.0, 123.0, 126.0],
        "volume":[100.0, 100.0, 100.0, 100.0],
        "is_complete":[True, True, True, True],
    }))
    _candle(k, 33, close=128.0, open_=126.5, high=129.0, low=126.0, volume=80)
    sells = [f for f in _fills(k) if f.side == "SELL"]
    assert sells
    reason = [s.metadata.get("exit_reason") for s in _signals(k)
              if s.side == "SELL"]
    assert "divergence" in reason, f"got reasons {reason}"
