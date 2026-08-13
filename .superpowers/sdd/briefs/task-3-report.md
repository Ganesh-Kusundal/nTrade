# Task 3 Report: Divergence-exit per-bar benchmark (Python + TS mirror)

## Status: DONE

## Commit
- `746b9b7` — `fix: divergence exit per-bar benchmark (python + ts mirror)`
  (4 files: `ntrade/engines/strategies.py` +1/-1, `tests/test_valentini_leg_anchor.py` +39, `ui/src/lib/valentini.ts` +9, `ui/src/lib/__tests__/valentini.test.ts` +9)
- `3d52ab3` — `refactor: reuse _runner_long/_install_range_bars in divergence benchmark test` (reviewer-nit follow-up, test file only)

`strategies.py` staging: only the `impulse_volume` hunk was staged (via `git apply --cached` of the extracted hunk); the pre-existing uncommitted Task-5 WIP classes (`GainzCloneStrategy`/`VwapReclaimStrategy`) were left in the working tree, unstaged.

## What was done
- `ntrade/engines/strategies.py` `_emit_entry`: `impulse_volume` = the leg's per-bar **MEAN** (`leg["volume"].mean()`) instead of the SUM — `_divergence_exit` now compares a completed range bar's volume to `divergence_volume_mult ×` the leg's *average* bar volume, so normal higher-high bars no longer read as weak.
- `ui/src/lib/valentini.ts`: mirrored — `impulseVolume` = `sum(volume)/leg.length` (guarded `: 0`), identical semantics to Python.
- `tests/test_valentini_leg_anchor.py`: new regression test `test_divergence_not_fired_on_normal_volume_followthrough` (normal-volume higher-high must NOT exit via divergence) + retuned `test_volume_divergence_exit`.
- `ui/src/lib/__tests__/valentini.test.ts`: divergence/structure-break threshold comments updated to the mean benchmark.

## Test results
- Red (pre-fix): both divergence tests failed — the new test's injected 100-vol range bar was weak vs `0.6 × 4700` (leg-sum) and exited; the existing divergence test stopped firing once the threshold dropped to `0.6 × ~142 ≈ 85` (its injected bar volume 100 was no longer weak).
- Green:
  - `python -m pytest tests/test_valentini_leg_anchor.py -q` → `18 passed in 79.96s`
  - `python -m pytest tests/test_valentini_strategy.py -q` → `24 passed in 87.63s`
  - `python -m pytest tests/test_zero_parity_across_modes.py -q` → `3 passed in 96.48s` (zero-parity preserved)
  - `cd ui && npm test` → `Test Files 10 passed (10) / Tests 130 passed (130)`; `npm run typecheck` → clean

## Deviations from the brief (documented)
1. **PLAN DEFECT — brief's verbatim new-test fixture fails even after the fix.** The brief's test gave bars 31/32 `volume=1000`. With `_leg_start_idx=0` (no impulse bar in this setup), the leg mean includes the 1500-volume absorption bar → mean ≈ 197, threshold ≈ 118 > the injected range bar's 100 → divergence would STILL fire. Retuned: bars 31/32 at default volume 100 → leg mean ≈ 142, threshold ≈ 85, so an injected 100-vol range bar is genuinely "normal" and the test is a true red→green regression (fails on the SUM benchmark `0.6 × 4700`, passes on the mean).
2. **Existing `test_volume_divergence_exit` retuned.** Under the mean benchmark the threshold drops to ~85, so its injected range-bar last volume (100) no longer read as weak. Changed `100.0 → 50.0` (still well below 0.6 × 142 ≈ 85) and corrected the stale "~4700" comment.
3. **TS threshold comments.** The brief estimated a ~20-bar leg (mean ~555, threshold ~333); the actual fixture leg is 23 bars (sum 11100) → mean ≈ 483, threshold ≈ 290. Comments updated to the true numbers; fixture volumes (80 weak / 8000 strong) sit on the correct sides of 290.

## Review
Code review returned "the change is sound" with 2 minor nits: (1) the new test inlined setup that `_runner_long`/`_install_range_bars` already provide — fixed in `3d52ab3` (net −18/+9 in the test file, re-verified 18 passed); (2) document the fixture deviation — this report.

## Concerns
- The new test's margin (100 vs threshold ~85) is deterministic (fixed fixtures), so not fragile in practice.
- `ui/dist` build output was NOT regenerated (out of the 4-file scope; dist is committed separately).
