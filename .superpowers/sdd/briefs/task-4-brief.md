# Task 4 (T-015): Wire DhanAuthProvider.stop() into shutdown

**Goal:** Complete T-011 — the proactive token-refresh timer was built (DhanAuthProvider.stop() exists, `ntrade/brokers/dhan_auth_provider.py:76`) but its shutdown hook was never wired. Add `DhanBroker.stop()` that cancels it, and call it from `LiveRunner.stop()` so the daemon refresh thread never outlives the session.

(No YAGNI concern: this removes a real daemon-thread leak on session shutdown.)

## Changes

### 1. `ntrade/brokers/dhan.py` — add `stop()`
Add a method (place it near `connect()` / `set_clock()`):
```python
def stop(self) -> None:
    """Cancel the auth provider's proactive refresh timer (shutdown hook)."""
    auth = getattr(self, "_auth", None)
    if auth is not None and hasattr(auth, "stop"):
        auth.stop()
```

### 2. `ntrade/runner/live_runner.py` — call it in `stop()`
In `def stop(self, reason: str = "") -> None:` (currently ~line 135, calls `self.kernel.stop(reason=reason)`), after the kernel stop, iterate the session's brokers and stop them:
```python
for instrument in self.kernel.ctx.instruments_snapshot():
    broker = getattr(instrument, "broker_adapter", None)
    if broker is not None and hasattr(broker, "stop"):
        broker.stop()
```
Reuse the same snapshot iteration pattern the file already uses in `_on_risk_halted` (line 184). Do not introduce a new snapshot method. Keep the rest of `stop()` unchanged.

## Tests
Add to `tests/test_dhan_auth_unit.py` (or the file that best fits; if that file is for lower-level auth helpers and a broker-level test doesn't belong there, put it in `tests/test_dhan_broker.py`):
```python
def test_broker_stop_cancels_auth_timer():
    from unittest.mock import Mock
    from ntrade.brokers import DhanBroker
    broker = DhanBroker(connect=False)
    broker._auth = Mock()
    broker.stop()
    broker._auth.stop.assert_called_once()
```
Check that `DhanBroker(connect=False)` is constructible offline (it is — connect is skipped). Prefer `tests/test_dhan_auth_unit.py` per the plan; if imports are awkward there, use `tests/test_dhan_broker.py` and say so in the report.

## Verify
```
./.venv/bin/python -m pytest -q
```
Expected: 626 passing (baseline) + 1 new = 627.

## Commit
```
git add ntrade/brokers/dhan.py ntrade/runner/live_runner.py tests/<the test file you used>
git commit -m "T-015 wire DhanAuthProvider.stop into LiveRunner shutdown"
```
Only stage files you actually changed. Subject exactly `T-015 wire DhanAuthProvider.stop into LiveRunner shutdown`.

## Report
Write to `.superpowers/sdd/briefs/task-4-report.md`: commit hash, `git show --stat`, full-suite count, and which test file you added the test to (and why).