# Valentini AMT Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align ValentiniScalper with Fabio Valentini's AMT method: leg-anchored volume profile, an ATR floor on the stop/step, and volume-confirmed accumulation.

**Architecture:** All changes live in `ntrade/engines/strategies.py` (ValentiniScalper). Three phases: (1) the location volume profile is rebuilt per impulse leg instead of per session; (2) `step` is floored at 1× ATR so explicit small `range_size` values cannot produce sub-ATR stops; (3) accumulation additionally requires the recent 2-bar volume to clear a multiple of the prior median volume. No event/kernel/execution changes; fills stay MARKET at `reference_price`, so zero-parity holds.

**Tech Stack:** Python 3, pandas, the existing event-bus strategy kernel. Tests run with `python -m pytest`.

## Global Constraints

- Only modify `ntrade/engines/strategies.py` (implementation) and `tests/test_valentini_leg_anchor.py` (all new tests). No other source file changes.
- `tests/test_valentini_strategy.py` has uncommitted WIP (202 lines) that must NOT be staged or committed. All plan tests live in the new self-contained `tests/test_valentini_leg_anchor.py`, which redefines its own minimal helpers (kernel/candle/absorption/fixed-profile) — no imports from the WIP file.
- Existing constructor params must keep their defaults and semantics (`range_size`, `atr_period`, `warmup`, `abs_lookback`, etc.).
- Zero-parity: `tests/test_zero_parity_across_modes.py` must keep passing (`3 passed`) — fills remain MARKET at `reference_price`, the strategy emits the same signals for the same OHLCV.
- Existing `tests/test_valentini_strategy.py` must keep passing (`24 passed`) — the 24 current tests mock `build_volume_profile` or use data that does not trigger an impulse, so they are insensitive to the leg anchor. Do not change those tests.
- Keep it lazy: no new modules, no new knobs beyond `leg_impulse_mult` and `accum_volume_mult`, no refactors of unrelated code.
- Mark deliberate shortcuts with `ponytail:` comments.

---

### Task 1: Constructor params + state for the three phases

**Files:**
- Modify: `ntrade/engines/strategies.py:107-175` (constructor)

**Interfaces:**
- Consumes: nothing new.
- Produces: instance attrs `self.leg_impulse_mult: float`, `self.accum_volume_mult: float`, `self._atr: float`, `self._step: float`, `self._leg_start_idx: int` — used by Tasks 2-4.

- [ ] **Step 1: Add the two constructor params**

In `ValentiniScalper.__init__` (`ntrade/engines/strategies.py:107-118`), add two params after `cvd_confirm_bars: int = 3`:

```python
                 depth_imbalance_min: float | None = None,
                 require_cvd: bool = True, cvd_confirm_bars: int = 3,
                 leg_impulse_mult: float = 2.0,
                 accum_volume_mult: float = 1.5):
```

- [ ] **Step 2: Assign the params**

In the constructor body, after `self.cvd_confirm_bars = max(1, int(cvd_confirm_bars))` (`strategies.py:155`), add:

```python
        self.leg_impulse_mult = max(1.0, float(leg_impulse_mult))
        self.accum_volume_mult = max(0.0, float(accum_volume_mult))
```

- [ ] **Step 3: Add the new state attrs**

In the internal-state block (after `self._pending_age: int = 0` at `strategies.py:175`), add:

```python
        self._atr: float = 0.0            # current ATR, recomputed per candle
        self._step: float = 1.0           # step = max(range_size, ATR) — ATR floor
        self._leg_start_idx: int = 0      # row index where the current impulse leg began
```

- [ ] **Step 4: Run the existing strategy tests to confirm no regression from the constructor change**

Run: `python -m pytest tests/test_valentini_strategy.py -q`
Expected: `24 passed` (new attrs are inert until Tasks 2-4 use them).
- [ ] **Step 5: Commit**

```bash
git add ntrade/engines/strategies.py
git commit -m "feat: valentini leg-anchor/ATR/volume knobs and state"
```

---

### Task 2: ATR floor on step

**Files:**
- Modify: `ntrade/engines/strategies.py:16` (import), `:263-267` (compute), `:277` (window step), `:289-290` (profile step), `:330` (`_update_phase` step), `:430` (`_emit_entry` step)

**Interfaces:**
- Consumes: `self._atr` (set here), `self._step` (set here).
- Produces: `self._step` used by Task 3 (profile bucket width) and Task 4 (accumulation window).

- [ ] **Step 1: Write the failing test**

Create `tests/test_valentini_leg_anchor.py` with self-contained helpers and the first test:

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

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_step_floored_to_atr_when_range_below_atr -q`
Expected: FAIL — `strat._atr` is `0.0` (attr does not exist) → AttributeError.

- [ ] **Step 3: Import `atr`**

`strategies.py:16` currently imports `from ntrade.domain.analytics.indicators import vwap, vwap_bands`. Change to:

```python
from ntrade.domain.analytics.indicators import atr, vwap, vwap_bands
```

- [ ] **Step 4: Compute `_atr` and `_step` per candle**

In `on_candle_closed`, after `self._range_size` is set (currently at `strategies.py:266-267`), insert:

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

Place this *after* `self._range_size = self.range_size or calc_auto_range(...)` and *before* `step = self._range_size or 1.0` at line 277.

- [ ] **Step 5: Replace the three step sites**

Line 277 (`step = self._range_size or 1.0` in `on_candle_closed`) → `step = self._step`.

Line 289-290 (`self._profile = build_volume_profile(frame, step=self._range_size or None)`) → `step=self._step`:

```python
            self._profile = build_volume_profile(
                frame, step=self._step)
```

Line 330 (`step = self._range_size or 1.0` in `_update_phase`) → `step = self._step`.

Line 430 (`step = self._range_size or 1.0` in `_emit_entry`) → `step = self._step`.

- [ ] **Step 6: Run the new test to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: PASS — 1 passed (the single ATR test).

- [ ] **Step 7: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: floor valentini step at 1x ATR (sub-ATR stop guard)"
```

---

### Task 3: Leg-anchored volume profile

**Files:**
- Modify: `ntrade/engines/strategies.py:271-276` (session reset), `:289-290` (profile build)

**Interfaces:**
- Consumes: `self._leg_start_idx`, `self._step`.
- Produces: `self._profile` over `frame.iloc[self._leg_start_idx:]`.

Design note: a range bar closes the moment its span reaches `range_size`
(`range_bars.py:97`), so a completed range bar's span is always ≈`range_size` —
it can never reach `2 × range_size`. An impulse must therefore be detected on
**1m candle span**, not range-bar span.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_valentini_leg_anchor.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_impulse_candle_reanchors_leg -q`
Expected: FAIL — `strat._leg_start_idx` is `0` (attr exists from Task 1, never updated).

- [ ] **Step 3: Reset leg index on session change**

At the new-day block in `on_candle_closed` (`strategies.py:271-276`), inside the `if key != self._session_key:` branch after `self._profile = None`, add:

```python
            self._leg_start_idx = 0
```

- [ ] **Step 4: Detect the impulse leg and build the profile on the leg slice**

Replace the profile build at `strategies.py:289-290`:

```python
        # Guide "location": POC/VAH/VAL over the CURRENT LEG, not the whole
        # session. A new impulse leg starts at the last 1m candle whose span
        # >= leg_impulse_mult * range_size. Range-bar spans can't drive this
        # (they close at range_size by construction), so the impulse test is
        # on 1m candle span. ponytail: single-candle span heuristic; a real
        # leg detector (multi-candle momentum) would be over-engineering here.
        if not frame.empty:
            leg_frame = frame.iloc[self._leg_start_idx:]
            if self._leg_start_idx > 0 and not leg_frame.empty:
                self._profile = build_volume_profile(
                    leg_frame, step=self._step)
            else:
                self._profile = build_volume_profile(
                    frame, step=self._step)
```

- [ ] **Step 5: Advance the leg index on each new impulse**

In `on_candle_closed`, after `self._range_bars = build_range_bars(...)` (currently `strategies.py:278-280`) and before the profile build, add the leg scan. It reads 1m candle spans from `frame`:

```python
        # Advance the leg anchor: the last 1m candle with span >= impulse
        # threshold starts a fresh leg. Clamp to the windowed frame.
        imp_thr = self.leg_impulse_mult * self._step
        imp_idx = -1
        for i in range(len(frame) - 1, -1, -1):
            row = frame.iloc[i]
            if (float(row["high"]) - float(row["low"])) >= imp_thr:
                imp_idx = i
                break
        if imp_idx >= 0:
            self._leg_start_idx = imp_idx
        self._leg_start_idx = min(self._leg_start_idx, len(frame) - 1)
```

- [ ] **Step 6: Run the new test to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: PASS — 2 passed (ATR + leg anchor). The 24 pre-existing `test_valentini_strategy.py` tests still pass because they mock `build_volume_profile` or never produce an impulse.

- [ ] **Step 7: Run zero-parity to confirm no regression**

Run: `python -m pytest tests/test_zero_parity_across_modes.py -q`
Expected: `3 passed`. The synthetic frame's absorption bar (bar 30) has span 0.1 < 8, so no impulse is detected and the profile stays session-wide — entry at `111.0` preserved.

- [ ] **Step 8: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: leg-anchored volume profile for valentini location"
```

---

### Task 4: Volume-confirmed accumulation

**Files:**
- Modify: `ntrade/engines/strategies.py:342-348` (accumulation check)

**Interfaces:**
- Consumes: `self.accum_volume_mult`, `self._step`, `self._profile`.
- Produces: nothing new (phase transition `absorbing -> accumulating` now gated on volume).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_valentini_leg_anchor.py`:

```python
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
    # prior mean ~100): accumulation must NOT confirm on a dead retrace.
    _candle(k, 31, close=118.0, volume=30)
    _candle(k, 32, close=118.0, volume=30)
    assert strat.phase == "absorbing"
    # A high-volume test of the POC confirms the move -> accumulating.
    _candle(k, 33, close=118.0, volume=300)
    assert strat.phase == "accumulating"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_accumulation_requires_recent_volume -q`
Expected: FAIL — after bars 31/32, phase is `"accumulating"` (price-only check passes today).

- [ ] **Step 3: Gate accumulation on recent volume**

Replace the accumulation check at `strategies.py:347`:

```python
            recent_vol = float(frame["volume"].iloc[-2:].sum())
            prior_vol = frame["volume"].iloc[:-2]
            avg_vol = float(prior_vol.mean()) if len(prior_vol) else 0.0
            # Volume confirmation: the move back to the POC must carry real
            # participation (guide §4.1), not a dead drift. NaN/empty guard:
            # no prior history means no volume test to fail.
            vol_ok = (avg_vol <= 0
                      or recent_vol >= self.accum_volume_mult * avg_vol)
            if (elapsed >= 2 and abs(close - poc) <= 2 * step and vol_ok):
                self._phase = "accumulating"
```

Note: `frame` is not currently a local in `_update_phase`. Add at the top of `_update_phase` (after `close = float(event.close)`, `strategies.py:326`):

```python
        frame = pd.DataFrame(self._rows)
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: PASS — 3 passed.

- [ ] **Step 5: Verify zero-parity still passes**

Run: `python -m pytest tests/test_zero_parity_across_modes.py -q`
Expected: `3 passed`. In the synthetic frame, bars 30-31 volume is 3000+1000=4000 and prior mean ≈1000 → 4000 ≥ 1.5×1000 → accumulation still confirms; entry at `111.0` unchanged.

- [ ] **Step 6: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: volume-confirmed valentini accumulation"
```

---

### Task 5: Full suite + parity verification

**Files:**
- Test: run all relevant suites.

- [ ] **Step 1: Run the full strategy + parity + wiring suites**

Run: `python -m pytest tests/test_valentini_strategy.py tests/test_valentini_live_wiring.py tests/test_zero_parity_across_modes.py -q`
Expected: all pass. If `test_valentini_live_wiring.py` fails on a VAL/VAH expectation, the leg anchor moved a profile value — adjust the *test's* synthetic frame (not the strategy) so the absorption sits at the leg's VAL, mirroring the doc's guidance.

- [ ] **Step 2: Run the wider engine/risk suites that exercise the strategy path**

Run: `python -m pytest tests/test_engine_pipeline.py tests/test_strategy_runner.py tests/test_integration_pipeline.py tests/test_event_traceability.py tests/test_risk_breakers.py tests/test_backtest_risk_breaker.py -q`
Expected: all pass.

- [ ] **Step 3: Commit any test fixture adjustments (only if Step 1 required them)**

```bash
git add tests/
git commit -m "test: tune wiring fixtures for leg-anchored profile"
```

## Self-review notes

- **Spec coverage:** Phase 3 (leg profile) = Task 3; Phase 5 (ATR floor) = Task 2; Phase 6 (volume accumulation) = Task 4. Phase 1 (test data fix) and Phase 2 (risk gate) were already complete before this plan and are out of scope. ✔- **Placeholder scan:** no TBDs; every code step carries the full block. ✔
- **Type consistency:** `_atr`, `_step`, `_leg_start_idx` are defined in Task 1, computed in Tasks 2-3, consumed in Tasks 3-4 — same names throughout. `leg_impulse_mult`/`accum_volume_mult` defined in Task 1, used in Tasks 3/4. ✔
- **ATR floor is lazy:** `calc_auto_range` already returns ≈1× ATR; the floor only clamps an explicit `range_size` below ATR, which is exactly the IL&O-contract case the review targeted. ✔
- **Leg anchor is lazy:** single-candle span heuristic on 1m candles (range-bar spans can't reach 2× by construction). Multi-candle momentum detection deferred — `ponytail:` comment documents the ceiling. ✔
- **Volume test is lazy:** 2-bar sum vs prior mean; NaN/empty-guarded so cold start can't block. ✔