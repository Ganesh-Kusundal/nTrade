# Task 3 Report — B-008: scanners read canonical indicator keys

**Status:** DONE
**Branch:** `g4-parity-complexity-batch`

## What changed

Canonical indicator keys are now what the built-in scanners read, and the
pipeline emits the one key the spike scanner needs. Three dead branches
eliminated.

### `ntrade/scanners/builtin.py`
- `MomentumScanner.scan`: reads `rsi_14` instead of phantom `rsi`
  (builtin.py:131). Price-change fallback unchanged. `indicator_values`
  summary key updated to `rsi_14` for consistency.
- `BreakoutScanner.scan`: reads `stx_10_3` instead of phantom `supertrend`
  (builtin.py:174); high/low fallback unchanged. `indicator_values` summary
  reads `stx_10_3` and `atr_14` instead of `supertrend`/`atr` (builtin.py:201).
  Class docstring updated to name the canonical keys.
- `VolumeSpikeScanner`: no change needed — it reads `avg_volume`, which is now
  genuinely emitted by the pipeline.

### `ntrade/domain/analytics/indicators.py`
- `compute_bundle` now emits `avg_volume = float(df["volume"].astype(float).mean())`
  (indicators.py:181), wrapped in the existing `_capture` helper so a missing
  `volume` column logs a warning (L1) instead of crashing — same pattern as the
  other indicators. The spike-multiplier branch is now reachable end-to-end.

### Tests (updated first, red → green)
- `tests/test_scanner.py`
  - `test_spike_via_avg_volume` (`{"avg_volume": 100_000}`) — unchanged; the
    key is no longer phantom now that `compute_bundle` emits it.
  - `test_rsi_momentum` `{"rsi": 72.0}` → `{"rsi_14": 72.0}`.
  - `test_supertrend_breakout` `{"supertrend": 1650.0}` → `{"stx_10_3": 1650.0}`.
- `tests/test_indicators.py`
  - `test_compute_bundle` asserts `"avg_volume" in bundle` and
    `bundle["avg_volume"] == 1000.0` (fixture has `"volume": [1000] * 50`).

## Test commands and output

Red phase (targeted, after test edits only):
```
$ ./.venv/bin/python -m pytest -q tests/test_scanner.py tests/test_indicators.py
3 failed, 42 passed in 1.88s   # test_rsi_momentum, test_supertrend_breakout, test_compute_bundle
```

Green phase (targeted):
```
$ ./.venv/bin/python -m pytest -q tests/test_scanner.py tests/test_indicators.py
45 passed in 1.84s
```

Full suite:
```
$ ./.venv/bin/python -m pytest -q
... [100%]
622 passed in 5.99s
```

## Commit hashes
- `42cbbf5` — scanners read canonical indicator keys; emit avg_volume (B-008)
- report commit — message: "H6 task-3 report: scanners read canonical indicator keys (B-008)"
## Concerns
- None blocking. `indicator_values` summary keys for momentum were also
  normalized (`rsi` → `rsi_14`) — behavior-equivalent, but a consumer of that
  summary key would see the canonical name now. No tests assert the old key.
- `avg_volume` is computed over the whole window passed to `compute_bundle`,
  i.e. the max-rows rolling window the IndicatorEngine feeds it; this matches
  the brief's intent ("mean of the volume column over the window").
- No new dependencies; domain layer unchanged in scope (pure pandas).
