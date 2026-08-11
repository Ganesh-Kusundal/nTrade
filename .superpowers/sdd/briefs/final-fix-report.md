# Final review fix round — whole-branch review fixes (g4-parity-complexity-batch)

**Status:** DONE · Commit `a3e9f28` (branch `g4-parity-complexity-batch`)

## What changed per finding

### Finding 1 (Important) — `ntrade/scanners/builtin.py::BreakoutScanner.scan`
- `stx_10_3` is now treated as a numeric band only when it is actually numeric:
  `if isinstance(stx, (int, float)) and stx > 0:`. The pipeline emits the supertrend
  DIRECTION string (`"up"`/`"down"`) for `stx_10_3`, so the numeric branch is dormant
  today — correct and safe.
- Restructured the fall-through: the high/low breakout is now `if not conditions and high > 0 and low > 0`
  (was `elif`), so a present-but-string `stx_10_3` no longer suppresses the working
  high/low path and no longer raises `TypeError`.
- `indicator_values` (a `dict[str, float]`) now only ever receives numeric values —
  `stx_10_3`/`atr_14` are included only when `isinstance(v, (int, float))`, so the
  direction string is filtered out.
- Class docstring rewritten to state the numeric-branch-is-dormant / high-low-fallback behavior.

### Finding 2 (Minor) — `ntrade/runner/gate.py::_equity_trace` docstring
- Rewrote the docstring: starts at `initial_cash`; updates the per-symbol
  `(quantity, ltp)` map on `PositionUpdatedEvent`; yields `(peak, eq)` on each settled
  `BalanceChangedEvent` (post-fill state, matching `RiskEngine.equity`); no yields for
  position-only updates. No behavior change.

### Finding 3 (Minor) — zero-cash safety in `ntrade/runner/gate.py::build_paper_report`
- The `(peak - eq) / peak * 100` drawdown division is now guarded by `if peak > 0:`,
  so a `peak <= 0` (e.g. `initial_cash == 0`) degrades to `0.0` instead of raising
  `ZeroDivisionError`. The initial-point yield is kept (per the plan); only the
  division is made safe.

### Finding 4 (Minor) — integration regression test
- Added `test_scanners_tolerate_real_indicator_bundles` to `tests/test_candle_engine.py`:
  runs `BacktestSimulator` over 21 bars (≥10 so `instrument._indicators` is populated
  with the real `rsi_14`/`atr_14`/string `stx_10_3`), appending a final bar that
  closes through the prior high so the high/low fallback fires and emits
  `indicator_values`; then calls `BreakoutScanner().scan(session)` plus
  `VolumeSpikeScanner` and `MomentumScanner`, asserting no raise, all returned
  `indicator_values` are numeric, and the string `stx_10_3` is excluded.
- Fails on pre-fix code with `TypeError: '>' not supported between instances of 'str' and 'int'`.

## TDD sequence

1. **Wrote the failing test first** and confirmed the red: `TypeError: '>' not supported between instances of 'str' and 'int'` at `ntrade/scanners/builtin.py:180`.
2. **Applied the fixes** (builtin.py guard/fallback + indicator_values filter + docstring; gate.py docstring + zero-peak guard).
3. **Confirmed green** on the new test + existing scanner tests, then the full suite.

## Exact commands and output tails

```
$ ./.venv/bin/python -m pytest -q tests/test_candle_engine.py::test_scanners_tolerate_real_indicator_bundles   # pre-fix (red)
...
E           TypeError: '>' not supported between instances of 'str' and 'int'
FAILED tests/test_candle_engine.py::test_scanners_tolerate_real_indicator_bundles
1 failed in 0.31s

$ ./.venv/bin/python -m pytest -q tests/test_candle_engine.py::test_scanners_tolerate_real_indicator_bundles tests/test_scanner.py   # post-fix (green)
...                                                                     [100%]
33 passed in 0.31s

$ ./.venv/bin/python -m pytest -q                                        # full suite
...                                                                     [100%]
624 passed in 5.79s
```

## Commit

`a3e9f28` — "Final review: guard BreakoutScanner stx type; gate zero-peak + docstring; scanner integration test"
(only `ntrade/scanners/builtin.py`, `ntrade/runner/gate.py`, `tests/test_candle_engine.py` staged; pre-existing `dhan*.py`/scratch left uncommitted).
