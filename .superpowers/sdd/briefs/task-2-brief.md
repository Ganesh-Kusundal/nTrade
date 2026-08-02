# Task 2 (T-012): Route DhanBroker data calls through DhanTransport

**Goal:** Make the D-005 transport abstraction real — every broker data-plane read delegates to `self._transport.*` instead of `self.tsl.*`, deleting the duplicated DhanMapper helpers in `dhan.py`. Order *placement* stays on `self.tsl`.

## CRITICAL READ FIRST — deviations from the plan doc

The task's official spec (docs/superpowers/plans/2026-08-02-integration-completeness-batch.md, Task 2) contains several **incorrect/deficient instructions**. Follow THIS brief, not the plan verbatim:

1. **DO NOT follow the plan's simplified `get_historical` snippet** (Step 3). It drops the DAY-FUT contract routing that tests `test_get_historical_day_routes_to_daily_endpoint_for_commodity`, `test_get_historical_day_nfo_index_option_uses_intraday_wrapper`, and `test_get_historical_day_fallback_empty_is_benign` prove must stay. Preserve the `_dhan_blocks_day`/`_historical_day_contract` split (they become thin transport delegators) and the `exchange = "INDEX" if instrument.KIND == "index" else instrument.exchange` mapping.
2. **Do NOT delete the `_first_float/_first_int/_first_str` imports** (dhan.py:32-34). `get_order_detail` (which stays on `self.tsl`) uses them. Delete only the LOCAL `_first_*` function defs (796-825); the imports then correctly resolve those names from `dhan_mapper`.
3. **Do NOT delete the `chain_from_dhan_df` import** (dhan.py:30). Delete the local `_chain_from_dhan_df` def (637-682) and switch `get_option_chain`'s call site (line 287) to the imported `chain_from_dhan_df(...)`. (The two implementations are byte-identical; tests prove parity.)
4. The routing-map table's bare `instrument.exchange` is WRONG for index-kind instruments. **Keep the `"INDEX" if index else "NFO"` / `"INDEX" if index else exchange` mapping** that the code already uses, for: `get_historical`, `get_long_term_historical`, `get_option_chain`, `get_expiry_list`.
5. **Three test files call data-plane methods on a transport-less DhanBroker** and will break: `tests/test_dhan_broker.py`, `tests/test_gap_closure.py`, `tests/test_mission_gaps.py`. Each builds brokers via `DhanBroker.__new__` + `broker.tsl = SimpleNamespace(...)` with NO `_transport`. You MUST wire `broker._transport = DhanTransport(broker.tsl)` in `make_broker` (test_dhan_broker.py, test_gap_closure.py) and in the two `test_dhan_get_instrument_metadata_*` tests in test_mission_gaps.py.
6. The error-match string on `test_get_quote_raises_on_failure` must change: transport wraps broker exceptions in `BrokerDataError("LTP fetch failed for <sym> after retries: <orig>")`. `match="LTP is 0"` does not match the raised stub's message. Change to `pytest.raises(RuntimeError, match="LTP fetch failed")`.

## Behavior contracts that MUST be preserved (existing tests prove them)
- `get_quote`: retry-flaky LTP (3 attempts — RetryPolicy default), enrich from quote data, stamp `timestamp=self._ts(now)`.
- `get_historical` DAY-FUT routing (commodity→daily endpoint; index-option→intraday; empty benign); normalize columns; reject unsupported timeframe via ValueError; apply days/start/end filters.
- `get_depth`: `Index()` → return None (before touching transport); websocket snapshot with timeout; tolerance of `price/quantity` legacy columns.
- `get_option_chain`: retry next expiry on None/raise/empty-frame; `chain_from_dhan_df` builds; resolve real expiry via `get_expiry_list`.
- `get_positions` / `get_balance`: RAISE on failed fetch (deliberate). `get_holdings` returns `[]` on failure; `get_orderbook`/`get_trade_book` return empty `OrderBook()`/`TradeBook()` on failure; `order_report`/`get_live_pnl`/`get_expiry_*`/`get_lot_size`/`get_future_script`/`get_ohlc`/`get_start_date`/`get_instrument_file` return their empty sentinels on failure.
- `get_instrument_metadata`: FUTCOM fallback via `SM_SYMBOL_NAME == instrument.symbol.upper()` must keep working (pass `underlying_symbol=instrument.symbol` through).

## dhan_transport.py changes (parity)
- `get_depth(symbol, exchange, timeout=5.0, *, now=None)` — add `now`; pass `now or self._ts()` into `DhanMapper.normalize_depth(..., now=...)`. (Broker passes its `now`.)
- `DhanMapper.filter_history` in dhan_mapper.py: add `asof: datetime | None = None` param; use `asof` for the `days`-cutoff anchor when provided, else `datetime.now()` (backward-compatible default). In `DhanTransport.get_historical`, `.get_long_term_historical`, `.get_daily_historical`, pass `asof=self._ts()` so replay (injected clock) stays deterministic. (Only touch dhan_mapper.py if it is the right place — check whether filter_history lives there; if it does, edit in place. If the plan/doc says a different location, keep the semantics identical.)
- Do NOT add RateLimiter to DhanTransport in this task (that's T-016).

## Delegations (broker method → transport; preserve exception semantics + exchange mapping)
```python
get_quote(self, instrument, *, now=None) -> Quote:
    self._ensure_tsl()
    quote = self._transport.get_quote(dhan_symbol(instrument))
    if quote.ltp <= 0:
        raise RuntimeError(f"get_quote failed for {instrument.symbol}: LTP is 0")
    return quote.with_update(timestamp=self._ts(now))
```
(Note: transport already raises BrokerDataError when LTP is 0, so the guard is defensive/unreachable — keep it per plan.)
```python
get_depth(self, instrument, timeout=5.0, *, now=None) -> MarketDepth|None:
    self._ensure_tsl()
    if instrument.KIND == "index": return None
    return self._transport.get_depth(instrument.symbol, instrument.exchange, timeout=timeout, now=now)
```
```python
get_historical(self, instrument, timeframe="5m", days=None, start=None, end=None) -> CandleSeries:
    self._ensure_tsl()
    if DhanMapper.map_timeframe(timeframe) == "DAY" and self._dhan_blocks_day(instrument):
        return self._historical_day_contract(instrument, days=days, start=start, end=end)
    exchange = "INDEX" if instrument.KIND == "index" else instrument.exchange
    start_s = start.strftime("%Y-%m-%d") if hasattr(start,"strftime") else start
    end_s = end.strftime("%Y-%m-%d") if hasattr(end,"strftime") else end
    return self._transport.get_historical(dhan_symbol(instrument), exchange, timeframe, days=days, start=start_s, end=end_s)

def _dhan_blocks_day(self, instrument) -> bool:
    return self._transport.blocks_day(dhan_symbol(instrument), instrument.exchange)

def _historical_day_contract(self, instrument, days=None, start=None, end=None) -> CandleSeries:
    start_s = start.strftime("%Y-%m-%d") if hasattr(start,"strftime") else start
    end_s = end.strftime("%Y-%m-%d") if hasattr(end,"strftime") else end
    df = self._transport.get_daily_historical(dhan_symbol(instrument), instrument.exchange, days=days, start=start_s, end=end_s)
    return CandleSeries(df, symbol=instrument.symbol, timeframe="1d")
```
`get_long_term_historical(instrument, timeframe="1d", from_date=None, to_date=None)`: map exchange `"INDEX" if index else instrument.exchange`; strftime-guard from/to; → `self._transport.get_long_term_historical(dhan_symbol, exchange, timeframe, from_date=from_s, to_date=to_s)`.

`get_option_chain(underlying, expiry=0, num_strikes=10, **kw)`: exchange = `"INDEX" if underlying.KIND=="index" else "NFO"`. Keep the retry-next-expiry loop and expiry-resolution block, but replace `self.tsl.get_option_chain(...)` with `self._transport.get_option_chain(dhan_symbol(underlying), exchange, expiry=attempt, num_strikes=num_strikes)` and `_chain_from_dhan_df(...)` with the imported `chain_from_dhan_df(...)`.

Portfolio/order-book/remainder (preserve existing index mapping and empty-sentinel + raise semantics):
```
get_ohlc            -> self._transport.get_ohlc(dhan_symbol(instrument))                      (keep try/except -> {})
get_start_date      -> self._transport.get_start_date()                                      (keep try/except -> None)
get_instrument_file -> self._transport.get_instrument_file()                                 (keep try/except -> None)
get_instrument_metadata -> self._transport.get_instrument_metadata(symbol, exchange, underlying_symbol=instrument.symbol)  (keep try/except -> {})
get_orderbook(now)  -> try: DhanMapper.normalize_orderbook(self._transport.get_orderbook(), now=self._ts(now)) except: OrderBook()
get_trade_book(now) -> try: DhanMapper.normalize_tradebook(self._transport.get_trade_book(), now=self._ts(now)) except: TradeBook()
order_report()      -> self._transport.order_report()                                        (has internal {} on failure)
get_live_pnl()      -> self._transport.get_live_pnl()                                        (has internal 0.0 on failure)
get_balance()       -> self._transport.get_balance()     # RAISES on failure (preserve)
get_positions()     -> self._transport.get_positions()   # RAISES on failure (preserve)
get_holdings()      -> try: return self._transport.get_holdings() except Exception: return []   # PRESERVE [] sentinel
get_expiry_list(i)  -> exchange = "INDEX" if i.KIND=="index" else "NFO"; return self._transport.get_expiry_list(i.symbol, exchange)   (keep try/except -> [])
get_expiry_date(i,opt_fut) -> self._transport.get_expiry_date(i.symbol, opt_fut)   (keep try/except -> [])
get_future_script(i,expiry) -> self._transport.get_future_script(i.symbol, expiry) (keep try/except -> None)
get_lot_size(i)     -> return self._transport.get_lot_size(dhan_symbol(i))         (keep try/except -> 0)
```
Add `self._ensure_tsl()` at the top of each routed data-plane method (no-op on test mocks; live token-freshness hardening). Do NOT alter order placement/cancel/modify/status/detail/executed-price methods (they stay on `self.tsl`).

## Deletions in dhan.py
Delete local (now dead) module helpers: `_chain_from_dhan_df`, `_f`, `_to_records`, `_dll_timeframe` + `_DHAN_TIMEFRAMES`, `_positions_from_df`, `_position_quantity`, `_holdings_from_df`, `_first_str`, `_first_int`, `_first_float`, `_normalize_history`, `_filter_history`, `_DAY_BLOCK_MAPPED_EXCHANGE`.
Import changes:
- KEEP (they become/should remain used): `DhanMapper` (line 29 for the `dhan_symbol = DhanMapper.to_trading_symbol` re-export line 51 and __init__), `chain_from_dhan_df` (line 30, now used in get_option_chain_), `_first_float`, `_first_int`, `_first_str` (lines 32-34, used by get_order_detail).
- DELETE: `to_records` (line 31) and `_f` (line 32) — become unused; `get_tradehull` (line 26) — unused import.
- Add: `from ntrade.brokers.dhan_mapper import DhanMapper, chain_from_dhan_df` — adjust the existing import statement (it's the same module block; reconcile the kept names and drop the deleted ones). Do not add any new third-party imports.

Note the re-export `dhan_symbol = DhanMapper.to_trading_symbol` at line 51 must remain (capabilities + tests import it).

## Test updates (only these four test files may be touched by this task)
1. `tests/test_dhan_broker.py`:
   - `make_broker`: add `broker._transport = DhanTransport(broker.tsl)` (import DhanTransport at top).
   - `test_get_quote_raises_on_failure`: change to `with pytest.raises(RuntimeError, match="LTP fetch failed"):`.
   - Timeframe tests (lines ~90-115): replace the three `from ntrade.brokers.dhan import _dhan_timeframe` imports with `from ntrade.brokers.dhan_mapper import DhanMapper` and assert `DhanMapper.map_timeframe(...)` (same assertions). (test_dhan_providers already covers map_timeframe.)
2. `tests/test_gap_closure.py`:
   - `make_broker` (line 21): add `broker._transport = DhanTransport(broker.tsl)` (import DhanTransport at top).
   - Line 184 (`from ntrade.brokers.dhan import _to_records`): change to `from ntrade.brokers.dhan_mapper import to_records` and update the two call lines (185-187) to `to_records(...)`.
3. `tests/test_options_analytics.py`:
   - Line 17 (`from ntrade.brokers.dhan import _chain_from_dhan_df`): change to `from ntrade.brokers.dhan_mapper import chain_from_dhan_df`; update the call at line 99 to `chain_from_dhan_df(underlying, df, atm=24550)`.
4. `tests/test_mission_gaps.py`: the two `test_dhan_get_instrument_metadata_*` tests — wire `broker._transport = DhanTransport(broker.tsl)` right after `broker.tsl = types.SimpleNamespace(instrument_df=idf)`. (Add a DhanTransport import handled locally.)

If the full suite reveals OTHER tests breaking from the routing (import or behavior), fix the smallest possible cause: wire `_transport` where a broker factory stubs `tsl`, or adjust a test that the routing legitimately changes — always preserving the behavior contracts above. Keep all edits within the four test files OR dhan.py / dhan_mapper.py / dhan_transport.py. Do not change unrelated tests.

## Verify
```
./.venv/bin/python -m pytest -q
```
Expected: 624+ (currently 625) passing. Tolerate expected test-count drift only if a test call was `on`got to the transport legitimately.

## Commit
Stage ONLY these files:
```
git add ntrade/brokers/dhan.py ntrade/brokers/dhan_mapper.py ntrade/brokers/dhan_transport.py tests/test_dhan_broker.py tests/test_gap_closure.py tests/test_options_analytics.py tests/test_mission_gaps.py
git commit -m "T-012 route DhanBroker data calls through DhanTransport"
```
Commit message subject exactly `T-012 route DhanBroker data calls through DhanTransport`. Do NOT commit `dhan_mapper.py` unless you actually modified it (the file_live `filter_history`/`asof` change); if you did not, omit it from the `git add`. Same for any file you did not touch — stage only files you changed.

## Report back
Write the report to `.superpowers/sdd/briefs/task-2-report.md` with: commit hash, `git show --stat` of the commit (confirm only intended files), full-suite count, list of test files changed, and a note of any deviation from this brief or surprises uncovered.