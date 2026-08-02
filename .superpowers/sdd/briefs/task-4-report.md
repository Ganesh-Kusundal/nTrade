# Task 4 (T-015) Report — wire DhanAuthProvider.stop() into shutdown

**Commit:** `b2cf7dfe876fde2068ed115c08afdb0eb19856c3`

## `git show --stat HEAD`
```
commit b2cf7dfe876fde2068ed115c08fd0eb19856c3
 ntrade/brokers/dhan.py       | 6 ++++++   (add DhanBroker.stop())
 ntrade/runner/live_runner.py | 4 ++++    (stop brokers in LiveRunner.stop())
 tests/test_dhan_broker.py    | 9 ++++++++ (new test)
 3 files changed, 19 insertions(+)
```

## Changes
- `ntrade/brokers/dhan.py:97` — added `DhanBroker.stop()` beside `connect()`/`set_clock()`; it calls `auth.stop()` on `_auth` when present, cancelling the DhanAuthProvider proactive refresh timer (`dhan_auth_provider.py:76`).
- `ntrade/runner/live_runner.py:140` — in `stop()`, after `kernel.stop(reason=reason)`, iterate `kernel.ctx.instruments_snapshot()` (same pattern as `_on_risk_halted`) and call `broker.stop()` on any `broker_adapter` exposing it.

## Test file choice

Added `test_broker_stop_cancels_auth_timer` to **`tests/test_dhan_broker.py`**, not `tests/test_dhan_auth_unit.py`. That file's docstring says it covers the lower-level `dhan_auth` helpers (module-level `dhan_auth` imports, monkeypatched Tradehull), so a broker-level test doesn't belong there. `tests/test_dhan_broker.py` already imports `DhanBroker` and has no network requirement.

## Verification

- Offline construction confirmed: `DhanBroker(connect=False)` (from `ntrade.brokers.dhan`) succeeds; the test stubs `_auth` with a `Mock` and asserts `stop.assert_called_once()`.
- Note: `from ntrade.brokers import DhanBroker` does **not** work — `ntrade/brokers/__init__.py` does not re-export it — so the test uses `DhanBroker` already imported in the file (`from ntrade.brokers.dhan import DhanBroker`), matching the brief's allowed alternative.

**Full-suite count:** `627 passed` (baseline 626 + 1 new).