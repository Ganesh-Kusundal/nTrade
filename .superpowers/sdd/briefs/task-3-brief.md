# Task 3 (T-016): Arm RateLimiter in transport + broker + feed hot paths

**Goal:** Give Dhan transport/broker/feed a real configurable API rate ceiling (D-006). `RateLimiter` exists (`ntrade/execution/retry.py:70`) with zero production references. `DhanTransport` uses `RetryPolicy` but throttles nothing. Arm it.

## Changes

### 1. `ntrade/brokers/dhan_transport.py`
Add a `rate_limiter` param to `__init__` (default `None`) and store it:
```python
def __init__(self, tsl, retry_policy=None, rate_limiter=None, clock=None):
    ...
    self._rate_limiter = rate_limiter   # new attribute
```
Add a helper and call it at the top of the LTP call inside the retry closure (the only hot per-tick path):
```python
def _throttled(self) -> None:
    """Respect the shared Dhan API rate ceiling, if configured."""
    if self._rate_limiter is not None:
        self._rate_limiter.wait()
```
In `_try_ltp` (the fn passed to `RetryPolicy.execute` inside `get_ltp`), call `self._throttled()` before `self._tsl.get_ltp_data(...)`. RetryPolicy already exists; do not alter it.

### 2. `ntrade/brokers/dhan.py`
Import `RateLimiter` (from `ntrade.execution.retry`). In `connect()` construct one shared limiter and hand it to the transport:
```python
def connect(self) -> "DhanBroker":
    self.tsl = self._auth.authenticate()
    self._rate_limiter = RateLimiter(calls_per_second=10.0)  # Dhan API ceiling
    self._transport = DhanTransport(self.tsl, rate_limiter=self._rate_limiter,
                                    clock=getattr(self, "_clock", None))
    self._connected = True
    return self
```
(Keep the method's existing docstring/return contract.)

### 3. `ntrade/sources/dhan_feed.py` — rate-limit restart/reconnect
The feed has NO manual reconnect loop (dhanhq owns the socket); a "reconnect" is a fresh `start()` after `stop()` (proven by `test_source_restart_after_stop_rebuilds_feed`). Arm it:
- In `__init__`, add `self._reconnect_limiter = RateLimiter(calls_per_second=0.5)` (import `RateLimiter` from `ntrade.execution.retry`). Set it in the same style as the existing `self._sleep = time.sleep` / `self._timer = time.monotonic` lines.
- In `start()`, before calling `self._build_feed()`, call `self._reconnect_limiter.wait()` so rapid `stop()`→`start()` cycles (reconnect storms) respect a 0.5/s ceiling. Keep the rest of `start()` unchanged.
- If the review/you find a real reconnect loop elsewhere in the file, gate that instead/additionally — but this file's current shape has no such loop.

## Tests
Add ONE test to `tests/test_retry.py` (in the `TestTransport`-style section, mirroring the existing `test_default_policy_used` / `test_get_ltp_*` tests):
```python
def test_transport_rate_limiter_throttles_ltp(self, mock_sleep):
    from ntrade.execution.retry import RateLimiter
    tsl = Mock()
    tsl.get_ltp_data.return_value = {"TCS": 100.0}
    limiter = RateLimiter(calls_per_second=100.0)
    transport = DhanTransport(tsl, rate_limiter=limiter)
    transport.get_ltp("TCS")
    assert limiter._last_time > 0  # the limiter was actually consulted
```
Use `Mock` or the existing imports/stubs already in test_retry.py — match the file's existing style. If `mock_sleep` is not needed, omit it; write the test in the file's prevailing style.

## Verify
```
./.venv/bin/python -m pytest -q
```
Expected: 625 passing (baseline). The new test adds one.

## Commit
Stage only files you changed:
```
git add ntrade/brokers/dhan_transport.py ntrade/brokers/dhan.py ntrade/sources/dhan_feed.py tests/test_retry.py
git commit -m "T-016 arm RateLimiter in transport and feed hot paths"
```
Only `git add` files you actually touched. Commit subject must be exactly `T-016 arm RateLimiter in transport and feed hot paths`.

## Report
Write to `.superpowers/sdd/briefs/task-3-report.md`: commit hash, `git show --stat HEAD`, full-suite count, and a one-line note on where the feed rate-limited (the `start()` restart gating) given dhanhq owns reconnection.