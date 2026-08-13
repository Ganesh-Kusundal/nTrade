# Task 6 Report: Full verification (priority-1 fixes batch)

## Status: DONE_WITH_CONCERNS

## Batch commits (this plan)
- Task 1: `bdee309` + fix `c5f5521` (paper broker authoritative balance/positions)
- Task 2: `a6da0b2` (backtest dedicated fill list)
- Task 3: `746b9b7` + nit `3d52ab3` (divergence-exit per-bar benchmark, Python + TS)
- Task 4: `555eed1` + review-fix `00c91f4` (session rollover clears rows + phase machine)
- Task 5: `6bdb620` + review-fix `570cdce` (WIP strategies hma/rsi + .time() + smoke tests)

## Test results
- `python -m pytest tests/test_valentini_leg_anchor.py -q` → `20 passed`
- `python -m pytest tests/test_valentini_strategy.py -q` → `24 passed`
- `python -m pytest tests/test_zero_parity_across_modes.py -q` → `3 passed` (parity preserved through every task)
- `python -m pytest tests/test_backtest_simulator.py tests/test_backtest_risk_breaker.py -q` → `4 passed`
- `python -m pytest tests/test_strategy_runner.py tests/test_wip_strategies.py -q` → `12 passed`
- `cd ui && npm test` → `Test Files 10 passed (10) / Tests 130 passed (130)`; `npm run typecheck` → clean
- Full Python suite `python -m pytest -q -p no:cacheprovider --ignore=tests/test_live_ws.py` → **1062 passed, 1 failed**

## The 1 failure — pre-existing, data-driven, NOT from this batch
`tests/test_orb_rvol_screener.py::test_fails_closed_when_0945_bar_missing` — the test asserts `screen_orb_rvol(store, as_of=date(2026, 8, 11))` raises `StoreStaleError` because "last full cash session in the store" was 2026-07-31. The **user's working-tree parquet refresh** (git status: `data/ohlcv/symbol=*/year=2026/month=08/*.parquet` modified) extended the store into August, so 2026-08-11 now has data and the stale precondition no longer holds. The failure is caused entirely by the uncommitted data changes (outside this batch's 5 fixes); the batch committed no data files and no code that this test touches. Flagged for the user: either refresh `_AS_OF`/`as_of` to a genuinely stale date or accept the store now covering August. Left untouched (a live-data integration test; changing its contract silently would be out of scope).

## Flake note
The first full-suite run showed `test_results_fills_not_truncated_by_bus_cap` as a second failure; it is fully deterministic (fixed synthetic frame), passes in isolation, in its file, and in both alphabetical predecessor pairings (`test_api_market` and `test_backtest_risk_breaker`), and passed the second full-suite run — a machine-load flake (first run 400s vs 250s under concurrent load).

## Review state
All 5 tasks reviewed clean after their fix rounds (reviewer findings addressed: Task 3 fixture defect + nit; Task 4 stale-absorption arming; Task 5 NaN stop guard + vacuous-smoke-test defect).
