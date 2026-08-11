# Task 2 Brief: ATR floor on step

## Where this fits

Project: nTrade — a Valentini AMT scalper on an event-bus kernel. Task 1 added
inert state (`_atr`, `_step`). This task computes them per candle and floors
`step` at 1× ATR. Task 3 uses `_step` for the profile bucket width; Task 4 uses
it for the accumulation window.

## Requirements (verbatim from the plan)

**Files:**
- Modify: `ntrade/engines/strategies.py:16` (import), `:263-267` (compute),
  `:277` (window step), `:289-290` (profile step), `:330` (`_update_phase` step),
  `:430` (`_emit_entry` step)
- Create: `tests/test_valentini_leg_anchor.py` (self-contained new test file)

**Step 1: Write the failing test**

Create `tests/test_valentini_leg_anchor.py` with self-contained helpers and the
first test (exact code):

```python
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
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_step_floored_to_atr_when_range_below_atr -q`
Expected: FAIL — `strat._atr` is `0.0` (attr exists but is never updated) →
`assert strat._atr > 1.0` fails.

**Step 3: Import `atr`**

`strategies.py:16` currently imports `from ntrade.domain.analytics.indicators import vwap, vwap_bands`. Change to:

```python
from ntrade.domain.analytics.indicators import atr, vwap, vwap_bands
```

**Step 4: Compute `_atr` and `_step` per candle**

In `on_candle_closed`, after `self._range_size` is set (currently at
`strategies.py:266-267`), insert:

```python
        # ATR floor: step can never be tighter than 1x ATR (low-tick IL&O
        # contracts would otherwise get sub-ATR stops from a small explicit
        # range_size). calc_auto_range already yields ~1x ATR, so this only
        # clamps explicit range_size values.
        a = atr(frame, self.atr_period)
        self._atr = 0.0
        if len(a) and pd.notna(a.iloc[-1]) and a.iloc[-1] > 0:
            self._atr = float(a.iloc[-1])
        self._step = max(self._range_size or 1.0, self._atr or 0.0)
```

Place this *after* `self._range_size = self.range_size or calc_auto_range(...)`
and *before* `step = self._range_size or 1.0` at line 277.

**Step 5: Replace the three step sites**

Line 277 (`step = self._range_size or 1.0` in `on_candle_closed`) → `step = self._step`.

Line 289-290 (`self._profile = build_volume_profile(frame, step=self._range_size or None)`) → `step=self._step`:

```python
            self._profile = build_volume_profile(
                frame, step=self._step)
```

Line 330 (`step = self._range_size or 1.0` in `_update_phase`) → `step = self._step`.

Line 430 (`step = self._range_size or 1.0` in `_emit_entry`) → `step = self._step`.

**Step 6: Run the new test to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: PASS — 1 passed.

**Step 7: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: floor valentini step at 1x ATR (sub-ATR stop guard)"
```

## Global Constraints (apply to this task)

- Only modify `ntrade/engines/strategies.py` and create `tests/test_valentini_leg_anchor.py`.
- `tests/test_valentini_strategy.py` has uncommitted WIP that must NOT be
  staged or committed. Do NOT touch that file, and do NOT import from it.
- Existing constructor params must keep their defaults and semantics.
- Keep it lazy: no new modules, no refactors of unrelated code.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-2-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the pytest output line, and any concerns.
