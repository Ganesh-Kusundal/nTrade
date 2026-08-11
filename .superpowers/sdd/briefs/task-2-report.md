# Task 2 Report: ATR floor on step

- **Status:** DONE
- **Commit hash:** `641e28a3d138b1d57473c5f446617fa1f0db4d93`
- **Branch:** `integration-completeness`

## Test summary

- `python -m pytest tests/test_valentini_leg_anchor.py -q` → `1 passed in 0.81s`
- `python -m pytest tests/test_valentini_strategy.py -q` → `24 passed in 5.60s`

## What was done (in order)

1. Created `tests/test_valentini_leg_anchor.py` with the exact code from the
   brief (module docstring, `_kernel`, `_candle`, `_uptrend_bars`,
   `_absorption_bar`, `_fixed_profile` helpers, and the single test
   `test_step_floored_to_atr_when_range_below_atr`).
2. Verified the test fails on the inert state as expected:
   `assert 0.0 > 1.0` / `got 0.0` (1 failed).
3. `ntrade/engines/strategies.py` changes, all verbatim per the brief:
   - Import: `from ntrade.domain.analytics.indicators import atr, vwap, vwap_bands`
   - Per-candle `_atr`/`_step` computation inserted after the
     `self._range_size = self.range_size or calc_auto_range(...)` line and
     before `step = self._range_size or 1.0`.
   - Replaced all three `step = self._range_size or 1.0` sites (the
     `on_candle_closed` window step, the `_update_phase` step, the `_emit_entry`
     step) with `step = self._step`.
   - Replaced the profile call `build_volume_profile(frame, step=self._range_size or None)`
     with `build_volume_profile(frame, step=self._step)`.
4. New test passes: 1 passed.
5. Regression run passes: 24 passed (no regression).
6. Committed exactly `ntrade/engines/strategies.py` and
   `tests/test_valentini_leg_anchor.py` with the required message.

## Constraints honored

- Only the two allowed files were modified/created. Nothing else was staged.
- `tests/test_valentini_strategy.py` was not touched (byte-identical) and was
  not staged; it remains a working-tree modification alongside all other
  unrelated uncommitted changes.
- Constructor params, defaults, and semantics unchanged.

## Concerns

- None. Commit contains only the two intended files (2 files changed, 102
  insertions(+), 5 deletions(-)).
- Note for Task 3/4 consumers: `_step` now defaults to `max(range_size or 1.0, _atr or 0.0)`
  and is recomputed every candle; profile bucket width now uses `_step` (which
  differs from the prior `_range_size or None` only when an explicit tiny
  `range_size` was clamped by ATR, or when `_step` was previously `None`).
