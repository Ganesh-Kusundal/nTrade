# Task 7 (T-019) Report: retire speculative feed mode codes

**Commit:** `362842d65c04888a9e34572bc0c1f1e7a9a9f373`

## `git show --stat HEAD`
```
 ntrade/sources/dhan_feed.py    | 24 +++---------------------
 tests/test_dhan_feed_source.py | 25 ++++++++-----------------
 2 files changed, 11 insertions(+), 38 deletions(-)
```

## Changes
- `ntrade/sources/dhan_feed.py`:
  - Removed `_MODE_CODES` table, `_mode_code()` method, and the `mode`/`version` params + `self.mode`/`self.version` storage.
  - `_subscriptions()` hardcodes subscription code `21` (full data, carries depth).
  - `_build_feed()` passes `version="v2"` literally to `MarketFeed(...)`.
- `tests/test_dhan_feed_source.py`:
  - `test_source_injects_feed_factory`: dropped `mode="full"`; kept `== [(1, 2885, 21)]`.
  - Replaced `test_source_default_mode_is_full` with `test_feed_always_uses_full_mode_code` (asserts `_subscriptions() == [(1, 2885, 21)]` and no `version`/`mode` attrs).
  - Deleted `test_source_bad_mode_raises` and `test_source_depth_mode_rejected_under_v2` (ValueError path removed).

## Production caller audit
No production caller of `DhanMarketFeedSource` passes `mode=` or `version=`:
- `ntrade/runner/feeds.py:17` calls `DhanMarketFeedSource(kernel, **(live_kwargs or {}))` — `live_kwargs` only ever originates from CLI JSON in `scripts/live_runner_run.py`, whose `--live-kwargs` default is `"{}"` (no mode/version). No config fills mode/version.
- Remaining `mode=...` matches in `ntrade/` are unrelated (`TradingSession`/`Tradehull`/kernel session modes).

No other file needed changing.

## Suite
```
./.venv/bin/python -m pytest -q
630 passed in ~24s
```
Net change from baseline 632: `-2` (deleted bad-mode + depth-mode tests; default-mode test replaced, not added). Actual final count: **630 passing**.