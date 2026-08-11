"""ValentiniScalper AMT-correction tests (leg anchor / ATR step / volume accumulation).

Self-contained: redefines its own kernel/candle helpers so it does not import
from tests/test_valentini_strategy.py (which carries unrelated uncommitted WIP).
"""

from datetime import datetime, timedelta

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
