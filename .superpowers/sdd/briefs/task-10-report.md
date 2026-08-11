# Task 10 (T-021) Report: Trim StrategyRunner to the documented add/release surface

## Status: COMPLETE

## Commit
- Hash: `66f1bb7` (`66f1bb7a344c4575d96285e906c06baffc2a2826`)
- Subject: `T-021 trim StrategyRunner to the documented add/release surface`
- Branch: `integration-completeness`

## git show --stat HEAD
```
ntrade/kernel/runner.py       | 32 +++------------------------
tests/test_strategy_runner.py | 50 +++++++++----------------------------------
2 files changed, 13 insertions(+), 69 deletions(-)
```

Staged ONLY the two intended files (`ntrade/kernel/runner.py` and `tests/test_strategy_runner.py`). Unrelated uncommitted working-tree files were left untouched.

## Removed methods
From `ntrade/kernel/runner.py`:
- `remove` — hot-detach a strategy and its scoped risk engine
- `enable` — enable toggle
- `disable` — disable toggle
- `_set_enabled` — internal shared toggle impl

## Retained
`add`, `_unique_name`, `_pause_global_risk`, `running`, `names`, `strategy`, `risk`, `status`, `__enter__`/`__exit__`, `start`, `stop`, `release`. Module docstring updated to drop the "hot attach/detach" claim.

## Tests
- `tests/test_strategy_runner.py` now runs 10 passed.
  - Removed: `test_runner_hot_detach_stops_dispatch` (used `.remove`), `test_runner_enable_disable_toggle` (used `.enable`/`.disable`).
  - Added: `test_runner_surface_is_minimal` (class-level introspection asserts `remove`/`enable`/`disable` absent, `{"add","release"} <= members`).
  - Kept all add/names/status/strategy/risk/running/release/context-manager/`start`/`stop` coverage.
- Full suite: **630 passed** (baseline 631; −2 removed tests +1 added = 630). Green.

## Integration confirmation
- `TradingSession` uses only `self._runner.add(...)`, `release()`, `start()`, `stop()`, and exposes `.runner` — none of the removed methods were referenced. Verified via grep: no production callers of `.runner.remove/.enable/.disable`.
- `_risk_pause_count` refcount logic in `_pause_global_risk` (increment) and `release` (decrement-to-zero restore) is completely untouched.