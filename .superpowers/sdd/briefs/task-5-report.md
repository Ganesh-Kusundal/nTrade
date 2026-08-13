# Task 5 Report: WIP strategies — hma/rsi import + `.time()` paren + smoke tests

## Status: DONE

## Commit
`6bdb620` — `fix: import hma/rsi + .time() paren for WIP strategies (smoke-tested)` (3 files: `ntrade/engines/strategies.py` +283, `ntrade/domain/analytics/indicators.py` +36, `tests/test_wip_strategies.py` +37)
`570cdce` — `fix: NaN-proof ATR stop guard in GainzClone._enter` (review-fix round)

## What was done
- `ntrade/engines/strategies.py`:
  - Import line: `from ntrade.domain.analytics.indicators import atr, vwap, vwap_bands` → `import atr, hma, rsi, vwap, vwap_bands` (GainzClone uses `hma`/`atr`/`rsi`; VwapReclaim uses `rsi`; `wma` is not referenced by the WIP strategies so it was NOT added — keep it lazy).
  - `GainzCloneStrategy.__init__`: `self.session_end = datetime.strptime(session_end, "%H:%M").time` → `.time()` (was a bound method; the `in_session` comparison raised TypeError on every candle).
- `ntrade/domain/analytics/indicators.py`: pre-existing uncommitted WIP — `wma` + `hma` (vectorized) + `compute_bundle` `hma_period` wiring. Committed as part of this unit (see deviation below).
- `tests/test_wip_strategies.py`: parameterized smoke test running each WIP strategy through `BacktestSimulator`.

## Test results
- Red (pre-fix): both parameterized cases failed —
  - GainzClone: TypeError (`'<=' not supported between datetime.time and builtin_function_or_method`) on every candle; the strategy engine SWALLOWS hook exceptions (logs + `_error_count`), so `_bars` never grew (0/400).
  - VwapReclaim: `NameError: name 'rsi' is not defined` past warmup (swallowed).
- Green: `python -m pytest tests/test_wip_strategies.py -q` → `2 passed`; regression `tests/test_strategy_runner.py tests/test_wip_strategies.py` → `12 passed`; `tests/test_valentini_strategy.py` → `24 passed`.

## Deviations from the brief (documented)
1. **PLAN DEFECT — the brief's verbatim smoke test passed even on broken code.** The strategy engine catches and logs hook exceptions per-strategy (`strategy._error_count` bumped, "strategy X failed on CandleClosedEvent (errors=N)") so `sim.run(df)` returns a result regardless. The brief's `assert res is not None` is vacuous. Strengthened to: `len(strat._bars) == len(df)` (catches the `.time` TypeError, which fires before the append) AND `getattr(strat, "_error_count", 0) == 0` (catches the swallowed hma/rsi NameError past warmup). Verified red→green on both cases.
2. **Staged the WIP unit, not just the two hunks (deviates from "stage ONLY the intended hunks").** The `.time()` paren fix lives INSIDE the uncommitted `GainzCloneStrategy` block — `git apply --cached` of just that line is impossible (the line doesn't exist at HEAD). Committing only the import hunk + test file would leave the committed tree import-broken on two counts (test imports a class absent from HEAD; strategies.py imports `hma` absent from committed indicators.py). The only self-consistent commit bundles the WIP classes + `wma`/`hma` indicator functions with their fixes — the pre-existing uncommitted work in these two files is precisely the Task-5 subject (nothing unrelated: strategies.py WIP = GainzCloneStrategy + VwapReclaimStrategy only; indicators.py WIP = wma/hma + bundle wiring only). Unrelated uncommitted changes (data parquets, api/marketdata.py, data layer) remain unstaged.
3. The brief mentioned `wma` as a consumed import, but the WIP code never calls it — not imported (lazy).

## Review
Round 1 (commit `6bdb620`): reviewer confirmed sound (wma/hma math verified, `.time()` fix correct, smoke assertions discriminating) with one actionable fix — `GainzClone._enter`'s `sl_dist <= 0` guard misses NaN (`NaN <= 0` is False), which could arm a position with a NaN stop that `_manage_exit` can never exit. Applied in `570cdce` (`if not sl_dist > 0: return`), re-verified 2 passed. Minor non-blocking notes: compute_bundle hma key cast asymmetry (matches existing rsi/atr convention — left as-is), lazy numpy import inside `wma` (deliberate), and neither WIP strategy force-closes at session end (out of scope; flagged for the production decision).

## Concerns
- The committed WIP strategies are research harnesses (GainzClone is "the documented clone logic", not a recommendation). Smoke-tested only — no assertion on trade correctness, matching the plan's scope.
