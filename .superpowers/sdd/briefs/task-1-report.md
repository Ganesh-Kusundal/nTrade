# Task 1 Report (T-020) — Remove fake streaming no-ops from the capability surface

**Status:** DONE_WITH_CONCERNS

## What changed

- `ntrade/brokers/dhan.py` — deleted the `_market_feed` and `_order_update_stream`
  module-level functions (formerly lines 883-898) together with their
  `@capability("market_feed", ...)` / `@capability("order_update_stream", ...)`
  decorators. Verified via `grep -rn` that `subscribe`/`LiveStream`/`market_feed`/
  `order_update_stream` are now entirely unreferenced in `dhan.py`; no imports
  needed removal (the `subscribe(...)` calls were method calls on the base
  `BrokerAdapter`, not imports).
- `tests/test_dhan_broker.py` — appended `test_no_fake_streaming_capabilities`
  exactly as specified in the brief.

## Test commands and output

1. Failing-test step (`./.venv/bin/python -m pytest tests/test_dhan_broker.py::test_no_fake_streaming_capabilities -q`):
   `1 passed in 0.37s` — **PASS on first run, before deletion** (see concerns).
2. Post-deletion verification (same command): `1 passed in 0.36s`.
3. Full suite (`./.venv/bin/python -m pytest -q`): **625 passed in 6.12s**
   (baseline 624 + the new test; no failures).

## Commit

`d0908dc` — `T-020 remove fake streaming no-op capabilities`

Note: the commit also carried the pre-staged `check_connection.py` deletion that
was already in the index from the prior batch (3 files in commit); I staged only
the two brief-listed files via explicit `git add ntrade/brokers/dhan.py
tests/test_dhan_broker.py`.

## Concerns

- **The brief's "failing test" expectation was incorrect.** The two fake streaming
  functions are **module-level** `@capability`-decorated functions, not `DhanBroker`
  class attributes, so `hasattr(DhanBroker, "_market_feed")` is False both before
  and after deletion. The test as specified in the brief is therefore vacuous —
  it passed on first run and never actually guarded the deletion. The deletion is
  still verified by the repo-wide grep (zero references remain) and the full suite,
  but the test will not catch a regression. A meaningful test would assert the
  capability names are absent from `registered_capabilities()` (or that
  `instrument.broker.available()` excludes them). I did not alter the test beyond
  the brief's verbatim text per the "don't modify beyond the brief" rule.
- `check_connection.py` (pre-staged deletion) rode along into the commit because it
  was already in the index when this task started.
