# Task 11 (T-022/T-023/T-024) Report: Fix three high-severity review findings

## Status: COMPLETE

## Plan
`docs/superpowers/plans/2026-08-02-fix-three-review-findings.md`

## Changes (all in working tree, uncommitted — branch `integration-completeness`)

### T-022 — Default backtest LIMIT fills are now bar-aware (`ntrade/backtest/simulator.py`)
- `BacktestSimulator` now always installs `BarAwareExecution` with `policy=fill_policy or FillPolicy()`. The plain-`SimulatedExecution` branch (which filled any LIMIT at its limit price even when no bar traded through) and its now-unused import were removed. Module docstring documents the default.
- Existing callers verified safe: `test_backtest_simulator_produces_equity_curve`, `test_backtest_commissions_and_drawdown`, and `test_statutory_wiring` convergence all still fill at 103/108 (the fixture's bar opens equal the limit prices, so fill prices are unchanged); `test_futures_carry_costs` computes expectations from actual fill prices so it is self-consistent; `test_backtest_market_orders_ignore_policy` MARKET path is untouched (MARKET bypasses bar logic in `BarAwareExecution`).

### T-023 — EventStore torn-line tolerance (`ntrade/storage/event_store.py`)
- `_load` wraps `json.loads(line)` in `try/except json.JSONDecodeError`; a malformed line (e.g. the truncated final line left by a crash mid-append) is skipped with a `logger.warning` instead of failing the whole store. ResilientKernel recovery reads exactly when a crash happened, so this removes a self-defeating failure path.

### T-024 — Kill-switch DEACTIVATE on resume (`ntrade/runner/live_runner.py`)
- `LiveRunner` now subscribes `RiskResumedEvent` → `_on_risk_resumed`: clears `halted`, and when `kill_switched` calls `kill_switch(action="DEACTIVATE")` on every broker-backed instrument. Only when **all** deactivations succeed is `kill_switched` cleared — a single failure keeps `kill_switched=True`, sets `kill_switch_failed=True`, and logs critical (symmetric to the halt path).

## Tests added (4)
- `tests/test_replay_backtest.py::test_backtest_limit_fills_bar_aware_by_default` — no explicit `fill_policy`: a LIMIT far below every low → 0 fills; a LIMIT within range still fills at min(limit, open).
- `tests/test_replay_backtest.py::test_event_store_tolerates_torn_final_line` — valid line + truncated final line → loads the valid event, no crash.
- `tests/test_kill_switch_wiring.py::test_risk_resume_deactivates_kill_switch` — halt → `["ACTIVATE"]`, resume → `["ACTIVATE", "DEACTIVATE"]`, `halted`/`kill_switched` cleared.
- `tests/test_kill_switch_wiring.py::test_risk_resume_deactivate_failure_keeps_kill_switched` — DEACTIVATE raises → `kill_switched` stays True, `kill_switch_failed` True.

## Verify
```
./.venv/bin/python -m pytest -q
```
Full suite: **663 passed** (baseline 659 + 4 new). Reviewer round 1 flagged the multi-instrument `kill_switched` reset in `_on_risk_resumed`; fixed (all-or-nothing flag) and covered by the failure-path test. Review clean after fix round.

## Kanban
`.kanban/board.json` updated (T-022/T-023/T-024, status done; 57 tasks). Kanban CLI script (`kanban.py update`) is not present on disk — its data files were updated directly. `progress.md` updated.
