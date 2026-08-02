# Task 3 (T-016) Report — Arm RateLimiter in transport + broker + feed hot paths

## Commit
`b2f8f11`

## `git show --stat HEAD`
```
 ntrade/brokers/dhan.py           |  7 ++++++-
 ntrade/brokers/dhan_transport.py |  9 ++++++++-
 ntrade/sources/dhan_feed.py      |  3 +++
 tests/test_retry.py              | 11 +++++++++++
 4 files changed, 28 insertions(+), 2 deletions(-)
```

## Full suite
`626 passed` (baseline 625 + 1 new test `test_transport_rate_limiter_throttles_ltp`).

## Feed rate-limit gate placement
The limiter landed at the top of `start()` (`self._reconnect_limiter.wait()` immediately before `_build_feed()`), which gates every `stop()→start()` cycle — dhanhq owns the socket, so rapid restart storms are throttled at 0.5 calls/sec with no manual reconnect loop to gate instead.