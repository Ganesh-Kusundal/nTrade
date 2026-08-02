# Fix three high-severity review findings

Date: 2026-08-02. Branch: `integration-completeness`.
Source: full-project review (2026-08-02) — three high-severity findings.

## Findings

| ID | Finding | Severity |
|----|---------|----------|
| R-1 | Default backtest fills LIMIT orders at the limit price without trade-through confirmation — optimistic fills that live would never produce (`ntrade/backtest/simulator.py:93` only installs `BarAwareExecution` when `fill_policy` is passed) | High |
| R-2 | `EventStore._load` crashes on a torn final JSONL line — `ResilientKernel.recover()` reads the store exactly when a crash happened, so a crash mid-append (truncated last line → `JSONDecodeError`) fails recovery precisely in the scenario it exists for (`ntrade/storage/event_store.py:106-113`) | High |
| R-3 | LiveRunner kill switch is one-way — subscribes only `RiskHaltedEvent` → `kill_switch("ACTIVATE")`; `RiskResumedEvent` has zero consumers, so after a breaker trips and `resume()` is called the broker stays kill-switched and all live orders are silently blocked (`ntrade/runner/live_runner.py`) | High |

## Verified facts

- `BacktestSimulator.__init__` accepts `fill_policy: FillPolicy | None = None`; when `None` it installs plain `SimulatedExecution` which fills any LIMIT at `intent.price or 0.0` unconditionally (`ntrade/execution/simulator.py:90`). `FillPolicy`/`BarAwareExecution` already exist and are tested (`tests/test_replay_backtest.py`).
- `EventStore._load` iterates JSONL lines with `json.loads(line)` unguarded; unknown `__type__` is already tolerated (`_decode` returns None), but malformed JSON is not.
- `LiveRunner.__init__` subscribes `RiskHaltedEvent`, `OrderFilledEvent`, `HeartbeatEvent`, `FeedDisconnectedEvent`, `OrderTimeoutEvent`. No `RiskResumedEvent` consumer exists anywhere in `ntrade/` (verified by grep). `RiskEngine.resume()` publishes `RiskResumedEvent`.

## Changes

### 1. `ntrade/backtest/simulator.py` — bar-aware LIMIT fills by default
- `fill_policy` stays `None`-defaulted, but `None` now means "default `FillPolicy()`" (bar-aware): always install `BarAwareExecution` with `policy=fill_policy or FillPolicy()`.
- Remove the plain-`SimulatedExecution` branch and its now-unused import.
- Update the module docstring + `__init__` docstring: LIMIT orders only fill when a bar trades through the limit by default; `fill_policy` remains for custom policies.

### 2. `ntrade/storage/event_store.py` — torn-line tolerance
- Wrap `json.loads(line)` in `try/except json.JSONDecodeError`; log a warning and skip the malformed line (a crash mid-append leaves a truncated last line). Intact lines still load.

### 3. `ntrade/runner/live_runner.py` — DEACTIVATE on resume
- Import + subscribe `RiskResumedEvent`.
- `_on_risk_resumed`: clear `halted`, and when `kill_switched` re-arm the broker kill switch (`DEACTIVATE`) on every broker-backed instrument; on failure set `kill_switch_failed` and log critical. Symmetric with `_on_risk_halted`.

## Tests
- `tests/test_replay_backtest.py`: add `test_backtest_limit_fills_bar_aware_by_default` (a LIMIT far below every bar low → 0 fills, no explicit policy) and `test_event_store_tolerates_torn_final_line` (valid line + truncated last line → loads the valid one, no crash).
- `tests/test_kill_switch_wiring.py`: add `test_risk_resume_deactivates_kill_switch` (halt → ACTIVATE recorded, resume → DEACTIVATE recorded, `halted`/`kill_switched` clear).

## Verify
```
./.venv/bin/python -m pytest tests/test_replay_backtest.py tests/test_kill_switch_wiring.py -q
./.venv/bin/python -m pytest -q
```
Baseline 659. Report the actual suite count.

## Kanban / SDD
- Add T-022 (R-1), T-023 (R-2), T-024 (R-3) to `.kanban/board.json` (status done) + matching cards in `.kanban/state.json` (kanban CLI script not on disk — update its data files directly).
- Append `docs/superpowers/plans/` + `.superpowers/sdd/progress.md` + a brief report.
