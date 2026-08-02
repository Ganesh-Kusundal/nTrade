# Task 2 (T-012) — Implementation Report

## Commit
- Hash: `7fe832ac176274b5e00a369bd171c0e1bb597f8c`
- Subject: `T-012 route DhanBroker data calls through DhanTransport`

## `git show --stat HEAD`
```
 ntrade/brokers/dhan.py           | 517 ++++-----------------------------------
 ntrade/brokers/dhan_mapper.py    |  11 +-
 ntrade/brokers/dhan_transport.py |  13 +-
 tests/test_dhan_broker.py        |  36 +--
 tests/test_gap_closure.py        |   8 +-
 tests/test_live_execution.py     |   2 +
 tests/test_mission_gaps.py       |   4 +
 tests/test_options_analytics.py  |   4 +-
 8 files changed, 103 insertions(+), 492 deletions(-)
```

## Full-suite pass count

`./.venv/bin/python -m pytest -q` → **625 passed** (matches the 625 baseline; no test-count drift).

## Test files changed and why

1. **tests/test_dhan_broker.py** — `make_broker` now wires `broker._transport = DhanTransport(broker.tsl)`; `test_get_quote_raises_on_failure` match changed to `"LTP fetch failed"`; three `_dhan_timeframe` imports switched to exported `DhanMapper.map_timeframe`.
2. **tests/test_gap_closure.py** — `make_broker` wires `_transport`; local `_to_records` import switched to `dhan_mapper.to_records`.
3. **tests/test_options_analytics.py** — `_chain_from_dhan_df` import/call switched to exported `dhan_mapper.chain_from_dhan_df`.
4. **tests/test_mission_gaps.py** — both `test_dhan_get_instrument_metadata_*` tests wire `_transport` (local DhanTransport import added).
5. **tests/test_live_execution.py** (NOT one of the four listed in the brief) — its `make_broker` stubs `tsl` with no `_transport`; the newly-routed `get_positions`/`get_balance` relied on `self._transport.get_positions()` and raised `AttributeError: 'NoneType'`. Wired `broker._transport = DhanTransport(broker.tsl)` (the minimal fix the brief anticipated). **This is the ONLY other test file beyond the four that needed `_transport` wiring** — all removal/route touches were confined to the intended four.

## Deviations / surprises

- **Fix in `dhan_transport.py` (module code, allowed):** `get_instrument_metadata` and `blocks_day` referenced `DhanMapper.DAY_BLOCK_MAPPED_EXCHANGE`, but that constant is module-level in `dhan_mapper.py`, not a class attribute. This raised `AttributeError`, which the `except` swallowed and returned the wrong default (`True` for "blocks day" on NFO index options; `{}` for metadata). This was a pre-existing latent bug that only became visible once the broker routed these reads through the transport. Fixed by importing the module-level `DAY_BLOCK_MAPPED_EXCHANGE` and referencing it directly. (Otherwise `test_get_historical_day_nfo_index_option_uses_intraday_wrapper` and `test_dhan_get_instrument_metadata_from_file` failed.)
2. **Commit includes `tests/test_live_execution.py`** — the brief's `git add` list only named the four test files, but that factory was legitimately broken by the routing and required the minimal wiring. Added explicitly; nothing else from the dirty working tree was staged. All other uncommitted prior-batch files were left untouched (verified via `git show --stat HEAD` — exactly 8 files).
3. Order placement/cancel/modify/status/detail/executed-price methods were left on `self.tsl` per brief (verified: every remaining `self.tsl.` call is an order/execution path).
4. Days `asof`/`now` parity applied to `DhanMapper.filter_history` (in `dhan_mapper.py`) and passed as `asof=self._ts()` from the three transport history methods; `get_depth` gained a `now` kwarg passed to `normalize_depth`.