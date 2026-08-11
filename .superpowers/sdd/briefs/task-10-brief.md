# Task 10 (T-021): Trim StrategyRunner to the documented add/release surface

**Goal (recommended: trim):** `StrategyRunner` is a thin convenience over `TradingKernel`. Keep `add()` + delegating `release()`/`start()`/`stop()`; delete the test-only mutation surface (`remove`, `enable`, `disable`, `_set_enabled`). No production code calls the deleted methods (verified).

## Verified facts
- `ntrade/kernel/runner.py` (160 lines). Public methods: `add`, `_unique_name`(impl), `_pause_global_risk`(impl), `remove`, `enable`, `disable`, `_set_enabled`(impl), `running`, `names`, `strategy`, `risk`, `status`, `__enter__`, `__exit__`, `start`, `stop`, `release` (lines 33-160).
- Production users of `StrategyRunner`: `ntrade/kernel/trading_session.py` (line 68 constructs, line 238 `self._runner.add(...)`, line 278 exposes `.runner`). Only `add/release/start/stop` matter to it — NO `remove`/`enable`/`disable`.
- `tests/test_strategy_runner.py` uses `remove` (lines 120-126), `enable`/`disable` (lines ~138-148), plus `names`, `running`, `status`, `strategy`, `risk` (keep these).

## Changes
### 1. `ntrade/kernel/runner.py` — delete test-only mutation surface
Delete `remove` (71-80), `enable` (83-84), `disable` (86-87), and `_set_enabled` (89-94). Keep everything else: `add`, `_unique_name`, `_pause_global_risk`, `running`, `names`, `strategy`, `risk`, `status`, `__enter__`, `__exit__`, `start`, `stop`, `release`. Note: `running`/`names`/`strategy`/`risk`/`status` are read-only queries and stay (the plan's earlier broad list mentioned them, but Step 3 only mandates deleting `remove`/`enable`/`disable`/`set_enabled`). Do NOT remove release/enter/exit/start/stop.

### 2. `tests/test_strategy_runner.py`
- Remove/rework the tests that call `.remove(...)`, `.enable(...)`, `.disable(...)` (around lines 120-126 and 138-148). Keep the tests for `add`, `names`, `status`, `strategy`, `risk`, `running`, `release`, context-manager, start/stop.
- ADD a `test_runner_surface_is_minimal` (class-level introspection):
```python
def test_runner_surface_is_minimal():
    import inspect
    from ntrade.kernel.runner import StrategyRunner
    members = {m for m, _ in inspect.getmembers(StrategyRunner) if not m.startswith("_")}
    assert "remove" not in members
    assert "enable" not in members
    assert "disable" not in members
    assert {"add", "release"} <= members
```

## Verify
```
./.venv/bin/python -m pytest tests/test_strategy_runner.py -q
./.venv/bin/python -m pytest -q
```
Baseline 631. Confirm the runner test file passes and full suite stays green. Report the actual suite number.

### Commit (stage ONLY the two files; `git status` first — never stage unrelated uncommitted files):
```
git add ntrade/kernel/runner.py tests/test_strategy_runner.py
git commit -m "T-021 trim StrategyRunner to the documented add/release surface"
```
Subject EXACTLY: `T-021 trim StrategyRunner to the documented add/release surface`.

## Report
Write `.superpowers/sdd/briefs/task-10-report.md`: commit hash, `git show --stat`, full suite count, list of removed methods, and confirmation that TradingSession/TradingKernel integration still works (names/add/release unaffected) and `_risk_pause_count` refcount logic (release) is untouched.