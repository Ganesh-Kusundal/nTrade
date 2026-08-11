# Task 3 Report: Leg-anchored volume profile

## Status
DONE

## Commit
`e7a5e64` — `feat: leg-anchored volume profile for valentini location`

## What changed
- `tests/test_valentini_leg_anchor.py`: appended `_impulse_bar` helper +
  `test_impulse_candle_reanchors_leg` (+23 lines).
- `ntrade/engines/strategies.py`:
  - (a) session-change block: `self._leg_start_idx = 0` added after
    `self._profile = None` inside `if key != self._session_key:`.
  - (b) profile build block located by `Guide "location": POC/VAH/VAL over...`
    comment replaced with leg-slice version (`frame.iloc[self._leg_start_idx:]`,
    falling back to the full frame when `_leg_start_idx == 0` or the slice is
    empty).
  - (c) leg-scan loop added after `self._range_bars = build_range_bars(...)`
    and before the profile build: scans the 1m `frame` backward for the last
    candle with `(high - low) >= leg_impulse_mult * self._step`, sets
    `self._leg_start_idx = imp_idx`, and clamps to `len(frame) - 1`.
  - Impulse detection is on 1m candle span (per plan note), NOT range-bar span.

## Pytest result lines
- Single new test (before fix, expected FAIL): `FAILED tests/test_valentini_leg_anchor.py::test_impulse_candle_reanchors_leg` — `assert 0 == 30` (`_leg_start_idx` stayed 0)
- `python -m pytest tests/test_valentini_leg_anchor.py -q`:
  `..` / `2 passed in 0.99s`
- `python -m pytest tests/test_zero_parity_across_modes.py -q`:
  `...` / `3 passed in 83.54s (0:01:23)`
- `python -m pytest tests/test_valentini_strategy.py -q`:
  `........................` / `24 passed in 5.57s`

## Constraints honored
- Only the two allowed files were modified and staged
  (`git diff --cached --name-only` shows exactly `ntrade/engines/strategies.py`
  and `tests/test_valentini_leg_anchor.py`).
- `tests/test_valentini_strategy.py` was not touched and remains unstaged
  (still `M` after commit); zero-parity confirms its WIP state still passes.
- No new modules, no constructor/default changes.

## Concerns
- `self._leg_start_idx` is clamped to `len(frame) - 1` even when no impulse is
  found, so it can grow toward the windowed-frame length (max_window) but never
  exceed it — the profile slice stays non-empty in practice. When a leg slice is
  short, `build_volume_profile` is called with few bars; the fallback branch
  (`_leg_start_idx == 0`) keeps the warmup/session-wide behavior intact, and the
  zero-parity suite confirms entry at `111.0` is preserved.
