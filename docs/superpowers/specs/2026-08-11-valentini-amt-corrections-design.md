# Valentini AMT Corrections — Design

## Problem

The ValentiniScalper (`ntrade/engines/strategies.py`) implements the Triple-A
(absorption → accumulation → aggression) three-gate filter, but deviates from
Fabio Valentini's AMT method in three places:

1. **Location profile is session-wide, not leg-bound.** `_update_phase` builds
   the volume profile over the entire session window (`strategies.py:289-290`).
   Once a new impulse leg starts mid-session, the pre-impulse volume dilutes the
   location signal — the value edge (VAL/VAH) the absorption must test is no
   longer the edge of the leg that actually matters.
2. **The stop/step can be set below 1× ATR.** `step = self._range_size or 1.0`.
   For low-tick IL&O contracts (SILVERM, GOLDM, MCX) an explicit small
   `range_size` can produce sub-ATR stops — too tight for the scalp to survive
   noise.
3. **Accumulation is price-only.** It requires ≥2 bars near the session POC
   (`abs(close - poc) <= 2*step`, `strategies.py:347`) with no volume
   confirmation that the market is actually testing the POC with participation.

## Changes

All changes are in `ntrade/engines/strategies.py`. No event/kernel/execution
changes. Zero-parity invariants hold because the strategy is the only touched
code and fills remain MARKET at `reference_price`.

### Phase 3 — Leg-anchored volume profile (impulse anchor)

- New constructor param `leg_impulse_mult: float = 2.0`.
- Track `self._leg_start_idx` (index into `self._rows`).
- When the last 1m candle has span `high - low >= leg_impulse_mult * range_size`
  (an impulse), the leg restarts at that candle's row index. Non-impulse
  candles keep the previous leg. **Detected on 1m candle span, not range-bar
  span** — a range bar closes the moment its span reaches `range_size`
  (`range_bars.py:97`), so a completed range bar can never reach `2 × range_size`.
- `self._leg_start_idx` is clamped to `[0, len(_rows))` — `_rows` is trimmed to
  `max_window` and the index must survive that trim. Out of range → fall back
  to the full frame.
- `_update_phase` builds the profile over the leg slice:

  ```python
  self._profile = build_volume_profile(
      frame.iloc[self._leg_start_idx:], step=self._range_size or None)
  ```

  Falls back to the full frame when no leg is detected yet (warm-up).

- `ponytail:` comment: anchor threshold is a knob; 2.0 for index futures,
  raise for thin MCX contracts.

### Phase 5 — ATR floor on step

- Store `self._atr` per candle from `atr(frame, atr_period)` (already the
  analytic behind `calc_auto_range`, `range_bars.py:22`).
- Use `step = max(self._range_size or 1.0, self._atr or 0.0)` at the three
  sites: `strategies.py:277` (window), `:330` (gates), `_emit_entry:430`
  (stop width).
- `calc_auto_range` already returns ≈1× ATR, so this only clamps an explicit
  `range_size` below ATR — pure safety widening, no signal-count change for
  ATR-derived steps.

### Phase 6 — Volume-confirmed accumulation

- New constructor param `accum_volume_mult: float = 1.5`.
- Strengthen the accumulation test (`strategies.py:347`) with the frame's
  `volume` column:

  ```python
  frame = pd.DataFrame(self._rows)   # local in _update_phase
  recent_vol = float(frame["volume"].iloc[-2:].sum())
  prior_vol = frame["volume"].iloc[:-2]
  avg_vol = float(prior_vol.median()) if len(prior_vol) else 0.0
  vol_ok = (avg_vol <= 0
            or recent_vol >= self.accum_volume_mult * avg_vol)
  if (elapsed >= 2 and abs(close - poc) <= 2 * step and vol_ok):
      self._phase = "accumulating"
  ```

- Guard `avg_vol <= 0` (no history) → treat volume test as passed rather than
  blocking cold-start accumulation. Baseline is the **median** prior volume
  (not the mean): the absorption spike would inflate the mean and reject normal
  follow-through volume, breaking the existing strategy suite.

## Test impact

- **Zero-parity** (`tests/test_zero_parity_across_modes.py`): Phase 3 changes
  `POC/VAH/VAL`, so the synthetic frame may stop firing or shift the entry
  price asserted at `111.0`. If so, retune `_make_frame()` so the absorption
  still sits at the leg's VAL. Phases 5/6 leave current data firing
  (`max(4, 1)=4`; recent-2-bar 2000 vs 1500 threshold).
- **Wiring fixtures** (`tests/test_valentini_live_wiring.py`): adjust VAL
  expectations if phase-3 profile moves them.
- New: construction smoke test for the two new params (defaults preserved).

## Risks

- Phase 3 may reduce signals (leg profile is tighter than session profile) —
  that is the *correct* behavior per AMT.
- Leg detection re-runs every candle but is O(latest bars) — no perf concern.
- A leg anchored on a single impulse bar of one 1m candle can be thin; the
  `ponytail` knob + full-frame fallback handle it.