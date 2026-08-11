# Integration Completeness Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the 10 planned-but-unintegrated subsystems (kanban T-012..T-021) into the live path — finish the provider decomposition, observability consumers, resilience arming, and feed watchdog — instead of leaving the architectural deliverables test-only.

**Architecture:** The plan's architecture was already built (T-001..T-011, ARCHITECTURE.md) but never routed into the hot path: DhanBroker bypasses DhanTransport, production reads private `inst._quote` instead of the capability layer, R-004/R-005/R-006 observability events publish to zero subscribers, and DhanAuthProvider's refresh timer has no shutdown hook. Each task completes one delivery: route, subscribe, arm, or retire — with the retirement option where wiring would be pure indirection (YAGNI wins over speculative completion).

**Tech Stack:** Python 3.10+, Dhan-Tradehull, pandas, stdlib only (threading, dataclasses, abc). Test runner: `./.venv/bin/python -m pytest -q`.

## Global Constraints

- Test command is always `./.venv/bin/python -m pytest -q` (baseline: **624 passing**, run from `/Users/apple/Downloads/nTrade`).
- `rg` is not installed in this shell — use `grep -rn` if a search is needed.
- Events stay `@dataclass(frozen=True, kw_only=True)` extending `ntrade.events.base.Event`; `ts` comes from the kernel clock, never `datetime.now()`.
- Domain layer stays broker-agnostic; no new third-party dependencies.
- New timestamps in broker/transport code come from the injected clock (`self._ts()`), never `datetime.now()` directly.
- Kanban cards T-012..T-021 track each task; sync via `python3 /Users/apple/.agents/skills/kanban.cli/scripts/kanban.py task status <id> done` then `.../kanban.py update` after each task.
- Commit message style matches `git log --oneline` (short `<ID> subject` style, e.g. `T-012 route broker data calls through DhanTransport`).
- Working tree already has 25 uncommitted changes (prior batch) — do not commit files outside the task's own `Files:` list.

---

### Task 1: Remove fake streaming no-ops from the capability surface (T-020)

**Files:**
- Modify: `ntrade/brokers/dhan.py:883-898` (delete `_market_feed` + `_order_update_stream`)
- Test: `tests/test_dhan_broker.py`

**Interfaces:**
- Consumes: nothing — these two methods have zero callers repo-wide (verified: grep of `market_feed`/`order_update_stream` only hits dhan.py, capabilities.py, and `sources/market_feed.py` which is unrelated).
- Produces: a capability surface where every `@capability` method is either a real TSL passthrough or a live-path caller.

**Context:** `_market_feed` and `_order_update_stream` (dhan.py:883-898) call `BrokerAdapter.subscribe()` — which only sets a flag, no websocket/data path exists — then return `instrument.stream`. They fake streaming. The real streaming path is `sources/dhan_feed.py`.

- [ ] **Step 1: Write the failing test** — assert the two fake methods no longer exist on the broker:

```python
def test_no_fake_streaming_capabilities():
    from ntrade.brokers.dhan import DhanBroker
    assert not hasattr(DhanBroker, "_market_feed")
    assert not hasattr(DhanBroker, "_order_update_stream")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_dhan_broker.py::test_no_fake_streaming_capabilities -q`
Expected: FAIL (both `hasattr` are True today).

- [ ] **Step 3: Delete the two methods**

In `ntrade/brokers/dhan.py`, delete the `_market_feed` method and the `_order_update_stream` method (dhan.py:883-898) and their `@capability(...)` decorators. Also drop the `subscribe`/`LiveStream` imports that only these two used, if any become unreferenced (`grep -rn "subscribe" ntrade/brokers/dhan.py` — keep if the base class still needs it).

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_dhan_broker.py::test_no_fake_streaming_capabilities -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/brokers/dhan.py tests/test_dhan_broker.py
git commit -m "T-020 remove fake streaming no-op capabilities"
```

---

### Task 2: Route DhanBroker data calls through DhanTransport (T-012)

**Files:**
- Modify: `ntrade/brokers/dhan.py` (methods listed below; delete local mapper helpers at 637-837 and the shadowed imports at 28-36)
- Modify: `ntrade/brokers/dhan_transport.py` (add the `asof` param to `filter_history` if needed for parity)
- Modify: `tests/test_dhan_broker.py`, `tests/test_dhan_providers.py`

**Interfaces:**
- Consumes: `DhanTransport` methods with raw `(symbol, exchange)` signatures:
  - `get_ltp(symbol) -> float`, `get_quote(symbol) -> Quote`, `get_depth(symbol, exchange, timeout=5.0) -> MarketDepth | None`
  - `get_historical(symbol, exchange, timeframe, days=None, start=None, end=None) -> CandleSeries`
  - `get_long_term_historical(symbol, exchange, timeframe, from_date, to_date) -> pd.DataFrame`
  - `get_daily_historical(symbol, exchange, days, start, end) -> pd.DataFrame`
  - `get_option_chain(underlying, exchange, expiry=0, num_strikes=10) -> ...`
  - `get_expiry_list(underlying, exchange)`, `get_expiry_date(underlying, opt_fut)`, `get_future_script(underlying, expiry)`, `get_lot_size(symbol)`, `get_ohlc(symbol)`, `get_start_date()`, `get_instrument_file()`, `instrument_df`
  - `get_orderbook()`, `get_trade_book()`, `order_report()`, `get_live_pnl()`, `get_balance()`, `get_positions()`, `get_holdings()`, `get_instrument_metadata(symbol, exchange)`, `blocks_day(symbol, exchange)`
- Produces: `DhanBroker` whose data-plane reads delegate to `self._transport.*`; the local `_normalize_history`/`_filter_history`/`_positions_from_df`/`_holdings_from_df`/`_to_records`/`_f`/`_first_*` copies in dhan.py are deleted in favor of `DhanMapper` (transport already uses it). This completes T-005/D-005 (D-005: "Provider SDK leaks through capability layer — no transport abstraction").

**Context — why this is safe:** every data-plane method on the broker currently calls `self.tsl.<libmethod>` directly. `DhanTransport` implements the same calls with retry + normalization and is already constructed (`dhan.py:71`) and clock-wired (`set_clock`). The only production references to `_transport` are the tsl/clock setters — nothing reads through it yet. Routing the data plane removes the inline retry loop (dhan.py:114-124), deletes the DhanMapper duplication (637-837), and makes the D-005 abstraction real.

**Routing map (broker method → transport call):**

| Broker method (dhan.py) | Transport call |
|---|---|
| `get_quote(instrument, now)` | `self._transport.get_quote(dhan_symbol(instrument))` |
| `get_depth(instrument, timeout)` | `self._transport.get_depth(instrument.symbol, instrument.exchange, timeout=timeout)` |
| `get_historical(instrument, timeframe, days, start, end)` | `self._transport.get_historical(dhan_symbol(instrument), instrument.exchange, timeframe, days=days, start=start, end=end)` (convert datetime args to `%Y-%m-%d` str) |
| `_dhan_blocks_day(instrument)` | `self._transport.blocks_day(dhan_symbol(instrument), instrument.exchange)` |
| `_historical_day_contract(instrument, days, start, end)` | `self._transport.get_daily_historical(dhan_symbol(instrument), instrument.exchange, days=days, start=start, end=end)` |
| `get_option_chain(underlying, expiry, num_strikes)` | `self._transport.get_option_chain(dhan_symbol(underlying), underlying.exchange, expiry=expiry, num_strikes=num_strikes)` |
| `get_expiry_list(instrument)` | `self._transport.get_expiry_list(instrument.symbol, exchange)` |
| `get_expiry_date(instrument, opt_fut)` | `self._transport.get_expiry_date(instrument.symbol, opt_fut)` |
| `get_future_script(instrument, expiry)` | `self._transport.get_future_script(instrument.symbol, expiry)` |
| `get_lot_size(instrument)` | `self._transport.get_lot_size(dhan_symbol(instrument))` |
| `get_long_term_historical(...)` | `self._transport.get_long_term_historical(dhan_symbol(instrument), instrument.exchange, timeframe, from_date, to_date)` |
| `get_ohlc(instrument)` | `self._transport.get_ohlc(dhan_symbol(instrument))` |
| `get_start_date()` | `self._transport.get_start_date()` |
| `get_instrument_file()` | `self._transport.get_instrument_file()` |
| `get_instrument_metadata(instrument)` | `self._transport.get_instrument_metadata(instrument.symbol, instrument.exchange)` |
| `get_orderbook(now)` | `self._transport.get_orderbook()` |
| `get_trade_book(now)` | `self._transport.get_trade_book()` |
| `order_report()` | `self._transport.order_report()` |
| `get_live_pnl()` | `self._transport.get_live_pnl()` |
| `get_balance()` | `self._transport.get_balance()` |
| `get_positions()` | `self._transport.get_positions()` |
| `get_holdings()` | `self._transport.get_holdings()` |

Order placement (`place_order`/`cancel_order`/`modify_order`/`get_order_status`/`get_order_detail`/`get_executed_price*`) stays on `self.tsl` for this batch — it carries SEBI F&O super-order logic the transport doesn't replicate.

- [ ] **Step 1: Write the failing tests** — prove routing through a mock transport:

```python
from unittest.mock import Mock
from ntrade.brokers.dhan import DhanBroker

def _broker_with_mock_transport():
    broker = DhanBroker(connect=False)
    broker._auth = Mock()  # skip real auth
    broker._transport = Mock()
    return broker

def test_get_quote_routes_through_transport():
    broker = _broker_with_mock_transport()
    broker._transport.get_quote.return_value = Mock(ltp=123.4)
    inst = Mock(symbol="TCS", exchange="NSE")
    quote = broker.get_quote(inst)
    broker._transport.get_quote.assert_called_once()
    assert quote.ltp == 123.4

def test_get_historical_routes_through_transport():
    broker = _broker_with_mock_transport()
    from ntrade.domain.market.candles import CandleSeries
    broker._transport.get_historical.return_value = CandleSeries(
        None, symbol="TCS", timeframe="5m")
    inst = Mock(symbol="TCS", exchange="NSE")
    cs = broker.get_historical(inst, timeframe="5m")
    broker._transport.get_historical.assert_called_once()
    assert cs.timeframe == "5m"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_dhan_broker.py::test_get_quote_routes_through_transport tests/test_dhan_broker.py::test_get_historical_routes_through_transport -q`
Expected: FAIL (`AssertionError: assert False` — transport not called).

- [ ] **Step 3: Route `get_quote` and `get_historical` through the transport**

Replace the body of `get_quote` (dhan.py:107-130, including the inline 3-attempt retry loop) with:

```python
def get_quote(self, instrument: "Instrument", *, now: datetime | None = None) -> Quote:
    self._ensure_tsl()  # token still checked on every data call
    quote = self._transport.get_quote(dhan_symbol(instrument))
    if quote.ltp <= 0:
        raise RuntimeError(
            f"get_quote failed for {instrument.symbol}: LTP is 0"
        )
    return quote.with_update(timestamp=self._ts(now))
```

Replace the body of `get_historical` (dhan.py:189-208) with:

```python
def get_historical(self, instrument, timeframe="5m", days=None, start=None, end=None) -> CandleSeries:
    self._ensure_tsl()
    start_s = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else start
    end_s = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else end
    return self._transport.get_historical(
        dhan_symbol(instrument), instrument.exchange, timeframe,
        days=days, start=start_s, end=end_s,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: the same two tests → Expected: PASS.

- [ ] **Step 5: Route the remaining data-plane methods per the table**

Apply the same delegation to every row in the routing map. Keep `self._ensure_tsl()` at the top of each. Delete the local helpers that become unreferenced: `_chain_from_dhan_df` (637-682), `_f` (683-689), `_to_records` (690-731), `_dhan_timeframe` (732-747), `_positions_from_df` (748-768), `_position_quantity` (769-776), `_holdings_from_df` (777-795), `_first_str`/`_first_int`/`_first_float` (796-827), `_normalize_history` (828-836), `_filter_history` (837-846) — and delete the now-shadowed dead imports at dhan.py:28-36 (`chain_from_dhan_df, to_records, _f, _first_float, _first_int, _first_str`) plus `get_tradehull` (dhan.py:26) if unreferenced after this. Update `tests/test_options_analytics.py:17` and `tests/test_gap_closure.py:184` to import from `ntrade.brokers.dhan_mapper` instead of the deleted `dhan.py` privates.

- [ ] **Step 6: Run the full suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: 624+ passing. Fix any signature mismatch (transport `start`/`end` are `str | None`, brokers pass `datetime | None` — the `strftime` guard in Step 3 handles it).

- [ ] **Step 7: Commit**

```bash
git add ntrade/brokers/dhan.py ntrade/brokers/dhan_transport.py tests/
git commit -m "T-012 route DhanBroker data calls through DhanTransport"
```

---

### Task 3: Arm RateLimiter + RetryPolicy in hot paths (T-016)

**Files:**
- Modify: `ntrade/brokers/dhan_transport.py` (already uses `RetryPolicy`; add `RateLimiter`)
- Modify: `ntrade/brokers/dhan.py` (pass limiter to transport in `connect()`)
- Modify: `ntrade/sources/dhan_feed.py` (rate-limit reconnect / polling)
- Test: `tests/test_retry.py`, `tests/test_dhan_broker.py`

**Interfaces:**
- Consumes: `RateLimiter(calls_per_second)` → `.wait()` (retry.py:70-97); `RetryPolicy(max_retries, base_delay, ...)` → `.execute(fn)` (retry.py:19-67).
- Produces: Dhan API calls throttled at a configurable ceiling (D-006: "No rate limiter or retry policy as infrastructure — ad-hoc time.sleep in retry loops").

**Context:** `RateLimiter` is built (retry.py) but has zero production references. `DhanTransport` uses `RetryPolicy` but no rate ceiling. Dhan's `get_ltp_data` intermittently rate-limits (the inline retry in dhan.py:114-124 exists because of it).

- [ ] **Step 1: Write the failing test**

```python
def test_transport_rate_limiter_spaces_calls():
    from unittest.mock import Mock
    from ntrade.brokers.dhan_transport import DhanTransport
    from ntrade.execution.retry import RateLimiter
    tsl = Mock()
    tsl.get_ltp_data.return_value = {"TCS": 100.0}
    calls = []
    limiter = RateLimiter(calls_per_second=100.0)
    transport = DhanTransport(tsl, rate_limiter=limiter)
    transport.get_ltp("TCS")
    assert hasattr(limiter, "_last_time") and limiter._last_time > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_retry.py::test_transport_rate_limiter_spaces_calls -q`
Expected: FAIL (`TypeError: DhanTransport.__init__() got an unexpected keyword argument 'rate_limiter'`).

- [ ] **Step 3: Add the `rate_limiter` param to DhanTransport**

In `dhan_transport.py`, add to `__init__`:
```python
def __init__(self, tsl: Any, retry_policy: RetryPolicy | None = None,
             rate_limiter: "RateLimiter | None" = None, clock=None):
    self._tsl = tsl
    self._mapper = DhanMapper()
    self._retry_policy = retry_policy or RetryPolicy()
    self._rate_limiter = rate_limiter
    self._clock = clock
```
Add a helper and gate the LTP fetch through it:
```python
def _throttled(self) -> None:
    """Block to respect the shared Dhan API rate ceiling, if configured."""
    if self._rate_limiter is not None:
        self._rate_limiter.wait()
```
In `_try_ltp` (get_ltp), call `self._throttled()` before `self._tsl.get_ltp_data(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_retry.py::test_transport_rate_limiter_spaces_calls -q`
Expected: PASS.

- [ ] **Step 5: Wire a shared limiter in the broker**

In `dhan.py:connect()`, construct and share one limiter between broker and feed:
```python
def connect(self) -> "DhanBroker":
    self.tsl = self._auth.authenticate()
    self._rate_limiter = RateLimiter(calls_per_second=10.0)  # Dhan API ceiling
    self._transport = DhanTransport(self.tsl, rate_limiter=self._rate_limiter,
                                    clock=getattr(self, "_clock", None))
    self._connected = True
    return self
```
Import `RateLimiter` in dhan.py.

- [ ] **Step 6: Rate-limit feed reconnect in dhan_feed.py**

In `sources/dhan_feed.py`, in the websocket reconnect path (around the error handler that currently `pass`es), gate reconnects with `self._rate_limiter.wait()` (or a dedicated `RateLimiter(calls_per_second=0.5)` on the feed) before re-connecting.

- [ ] **Step 7: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/brokers/dhan_transport.py ntrade/brokers/dhan.py ntrade/sources/dhan_feed.py tests/test_retry.py
git commit -m "T-016 arm RateLimiter in transport and feed hot paths"
```

---

### Task 4: Wire DhanAuthProvider.stop() into shutdown (T-015)

**Files:**
- Modify: `ntrade/runner/live_runner.py` (stop path)
- Modify: `ntrade/brokers/dhan.py` (expose `stop()` on broker)
- Test: `tests/test_dhan_auth_unit.py`

**Interfaces:**
- Consumes: `DhanAuthProvider.stop()` (dhan_auth_provider.py:76-78) — cancels the proactive refresh timer.
- Produces: `LiveRunner.stop()` cancels the token-refresh timer so the daemon thread never outlives the session. Completes T-011 (proactive refresh was built, its shutdown half was never wired).

- [ ] **Step 1: Write the failing test**

```python
def test_broker_stop_cancels_auth_timer():
    from unittest.mock import Mock
    from ntrade.brokers.dhan import DhanBroker
    broker = DhanBroker(connect=False)
    broker._auth = Mock()
    broker.stop()
    broker._auth.stop.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_dhan_auth_unit.py::test_broker_stop_cancels_auth_timer -q`
Expected: FAIL (`AttributeError: 'DhanBroker' object has no attribute 'stop'`).

- [ ] **Step 3: Add `stop()` to DhanBroker and call it in LiveRunner.stop**

In `dhan.py`, add:
```python
def stop(self) -> None:
    """Cancel the auth provider's proactive refresh timer."""
    auth = getattr(self, "_auth", None)
    if auth is not None and hasattr(auth, "stop"):
        auth.stop()
```
In `live_runner.py:stop()` (after `self.kernel.stop(...)`), add:
```python
for instrument in self.kernel.ctx.instruments_snapshot():
    broker = getattr(instrument, "broker_adapter", None)
    if broker is not None and hasattr(broker, "stop"):
        broker.stop()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_dhan_auth_unit.py::test_broker_stop_cancels_auth_timer -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/brokers/dhan.py ntrade/runner/live_runner.py tests/test_dhan_auth_unit.py
git commit -m "T-015 wire DhanAuthProvider.stop into LiveRunner shutdown"
```

---

### Task 5: Attach consumers for R-006 observability events (T-014)

**Files:**
- Modify: `ntrade/runner/live_runner.py` (subscribe + act on the three events)
- Modify: `ntrade/engines/risk_engine.py` or `ntrade/execution/broker_executor.py` (OrderTimeout handling)
- Test: `tests/test_live_runner.py`, `tests/test_event_bus_clock.py`

**Interfaces:**
- Consumes: `HeartbeatEvent(tick_count, open_orders)` (lifecycle.py:47-52), `FeedDisconnectedEvent(reason)` (lifecycle.py:55-58), `OrderTimeoutEvent(order_id, symbol, exchange, side, quantity, age_seconds)` (order.py:80-90).
- Produces: every observability event has at least one consumer:
  - `HeartbeatEvent` → structured log (`logger.info("heartbeat tick_count=... open_orders=...")`).
  - `FeedDisconnectedEvent` → logger.warning + risk halt (feed is down).
  - `OrderTimeoutEvent` → logger.warning + cancel the stale PENDING order (R-005: "PENDING orders can sit forever").

- [ ] **Step 1: Write the failing tests**

```python
def test_feed_disconnected_halts_runner():
    from ntrade.events.lifecycle import FeedDisconnectedEvent
    from ntrade.events.risk import RiskHaltedEvent
    halted = []
    runner = _make_runner()
    runner.kernel.bus.subscribe(RiskHaltedEvent, lambda e: halted.append(e))
    runner.kernel.bus.publish(FeedDisconnectedEvent(reason="ws drop", ts=runner.kernel.clock.now()))
    assert halted, "FeedDisconnectedEvent must trip the risk halt"
```

```python
def test_order_timeout_cancels_stale_order():
    from ntrade.events.order import OrderTimeoutEvent
    cancelled = []
    runner = _make_runner()
    runner._on_order_timeout = lambda e: cancelled.append(e.order_id)
    runner.kernel.bus.publish(OrderTimeoutEvent(
        order_id="O1", symbol="TCS", exchange="NSE", side="BUY",
        quantity=10, age_seconds=120.0, ts=runner.kernel.clock.now()))
    assert cancelled == ["O1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_live_runner.py -k "feed_disconnected or order_timeout" -q`
Expected: FAIL (`AssertionError` — no subscriber).

- [ ] **Step 3: Subscribe and act in LiveRunner.__init__**

In `live_runner.py:__init__`, after the existing `RiskHaltedEvent` subscribe, add:
```python
from ntrade.events.lifecycle import FeedDisconnectedEvent
from ntrade.events.order import OrderTimeoutEvent
...
self.kernel.bus.subscribe(FeedDisconnectedEvent, self._on_feed_disconnected)
self.kernel.bus.subscribe(OrderTimeoutEvent, self._on_order_timeout)
```
Add the handlers:
```python
def _on_heartbeat(self, event) -> None:
    self.logger.info("heartbeat tick_count=%d open_orders=%d",
                     event.tick_count, event.open_orders)

def _on_feed_disconnected(self, event) -> None:
    self.logger.warning("feed disconnected: %s — halting", event.reason)
    self.kernel.bus.publish(RiskHaltedEvent(
        reason=f"feed disconnected: {event.reason}", ts=self.kernel.clock.now()))

def _on_order_timeout(self, event) -> None:
    self.logger.warning("order timeout: %s %s x%d aged %.0fs — cancelling",
                        event.side, event.symbol, event.quantity, event.age_seconds)
    self.kernel.cancel_order(event.order_id)  # guards against zombie PENDING orders
```
Subscribe `HeartbeatEvent` to `_on_heartbeat` as well.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_live_runner.py -k "feed_disconnected or order_timeout" -q`
Expected: PASS. If `kernel.cancel_order` isn't exposed on the kernel, resolve via the broker directly (see Task 2 note — order path stays on `self.tsl`).

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/runner/live_runner.py tests/test_live_runner.py
git commit -m "T-014 attach consumers for heartbeat/feed-drop/order-timeout events"
```

---

### Task 6: Wire feed watchdog reconnection (T-018)

**Files:**
- Modify: `ntrade/sources/dhan_feed.py` (reconnect on disconnect, publish `FeedDisconnectedEvent` then reconnect)
- Modify: `ntrade/domain/market/stream.py` (`LiveStream.reconnect()` lifecycle)
- Test: `tests/test_dhan_feed_source.py`, `tests/test_history_stream.py`

**Interfaces:**
- Consumes: `LiveStream` state machine (`SubscriptionState`), `FeedDisconnectedEvent(reason)`.
- Produces: a feed that re-subscribes after a drop instead of dying silently (D-013: "`DhanFeed._on_error` is literal pass — silent feed death with no reconnection"; R-004: "No feed watchdog or heartbeat — frozen kernel/feed goes undetected").

**Context:** `live_runner.py:_check_feed_watchdog` (154-174) already trips `RiskHaltedEvent` when ticks freeze — but nothing ever reconnects the websocket, so a single blip kills the day. The reconnect method already exists on the stream: `LiveStream.notify_reconnect()` (stream.py:95) — it's dead today (audit finding). Wire it.

- [ ] **Step 1: Write the failing test**

```python
def test_feed_reconnects_after_disconnect():
    from unittest.mock import Mock
    feed = _make_feed_with_mock_transport()
    feed._on_error(RuntimeError("ws closed"))
    assert feed._reconnect_called, "feed must attempt reconnect after error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_dhan_feed_source.py::test_feed_reconnects_after_disconnect -q`
Expected: FAIL (today `_on_error` is `pass`).

- [ ] **Step 3: Implement reconnect in dhan_feed.py**

Replace the `_on_error` pass-through with:
```python
def _on_error(self, exc: Exception) -> None:
    self.logger.warning("feed error: %s — attempting reconnect", exc)
    self.bus.publish(FeedDisconnectedEvent(reason=str(exc), ts=self._ts()))
    self._reconnect()

def _reconnect(self) -> None:
    self._rate_limiter.wait() if self._rate_limiter is not None else None
    try:
        self.stop()          # tear down the dead socket
        self.start()         # re-attach + re-subscribe all instruments
        self._reconnect_called = True
    except Exception:
        self.logger.error("reconnect failed — feed remains down")
```
Publish `HeartbeatEvent` from the re-established stream so the watchdog (T-014/T-018 pair) sees life again; reset `LiveRunner._last_tick_count` is not needed since `_check_feed_watchdog` uses total tick count across checks.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_dhan_feed_source.py::test_feed_reconnects_after_disconnect -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/sources/dhan_feed.py ntrade/domain/market/stream.py tests/test_dhan_feed_source.py
git commit -m "T-018 wire feed watchdog reconnect on disconnect"
```

---

### Task 7: DhanFeed mode matrix — wire or retire (T-019)

**Files:**
- Modify: `ntrade/sources/dhan_feed.py` (resolve the `_MODE_CODES` table)
- Test: `tests/test_dhan_feed_source.py`

**Interfaces:**
- Consumes: the `mode`/`version` params on `DhanMarketFeedSource.__init__`.
- Produces: one of (a) real `"ticker"`/`"quote"` lightweight subscriptions, or (b) the speculative mode codes removed.

**Decision (recommended: retire):** production (runner/feeds.py, scripts) only ever uses `mode="full"` (default) and `version="v2"`. The `"ticker"`/`"quote"` codes have zero callers; `mode="depth"` exists solely to raise `ValueError` (asserted in `test_dhan_feed_source.py:267`). Writing two more subscription paths is speculative — Dhan's websocket already delivers full snapshots under `code 21`. **Remove** the `_MODE_CODES` table, `_mode_code()`, and the `version` param; hardcode code `21`.

- [ ] **Step 1: Write the failing test**

```python
def test_feed_hardcodes_full_mode_code():
    from ntrade.sources.dhan_feed import DhanMarketFeedSource
    feed = DhanMarketFeedSource(tsl=Mock(), symbols=[("TCS", "NSE")])
    assert feed._mode_code() == 21
    assert not hasattr(feed, "version")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_dhan_feed_source.py::test_feed_hardcodes_full_mode_code -q`
Expected: FAIL (`_mode_code` returns `self._MODE_CODES[self.mode]` which equals 21 by luck, and `version` still exists).

- [ ] **Step 3: Retire the speculative modes**

Delete `_MODE_CODES`, `_mode_code()`, the `mode`/`version` params and their storage in `dhan_feed.py`. Replace `_mode_code()` call sites with the literal `21`. Delete the now-dead `assert mode == "depth"` ValueError branch and its test (`test_dhan_feed_source.py:267`).

- [ ] **Step 4: Run test to verify it passes**

Run: the test above → PASS.

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/sources/dhan_feed.py tests/test_dhan_feed_source.py
git commit -m "T-019 retire speculative feed mode codes; hardcode full data code 21"
```

---

### Task 8: Finalize the capability layer (T-013)

**Files:**
- Modify: `ntrade/kernel/trading_session.py` (read path)
- Modify: `ntrade/domain/instruments/capabilities.py` (remove unused facades)
- Modify: `ntrade/domain/instruments/base.py` (canonical accessors)
- Test: `tests/test_instruments.py`, `tests/test_domain_types.py`

**Interfaces:**
- Consumes: `Instrument._quote/_history/_stream` (current private read model).
- Produces: production reads the canonical capability accessors (`instrument.market`, `instrument.history`) — the T-002 decomposition made real — and the unintegrated `Trade`/`Analytics`/`Stream`/`Extension` facade objects are removed (test-only).

**Decision (recommended: wire the reads, retire the facades):** the audit found only `.market.refresh()` used in production and everything else test-only. Wiring production through a full facade adds indirection for no behavior change. The intended decomposition (D-001) is about *Instrument not being a god object* — so: give `Instrument` the two canonical read accessors production actually needs, route `trading_session.py`/`scanners`/`engines` through them, and delete the unused `TradeCapability`/`AnalyticsCapability`/`StreamCapability`/`ExtensionCapability` classes and `Instrument.trade`/`extension` properties.

- [ ] **Step 1: Write the failing test**

```python
def test_engines_read_through_canonical_accessor():
    from unittest.mock import Mock
    inst = Mock()
    inst.market = Mock()
    inst.market.quote = {"ltp": 100.0}
    # production must read market state via the accessor, not _quote:
    assert inst.market.quote["ltp"] == 100.0
```

(More meaningful integration test: build a `TradingSession`, refresh a paper instrument, assert `session.instruments` expose `.market.quote` without touching `_quote`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_instruments.py -k canonical -q`
Expected: FAIL or N/A — add the concrete failing test against a real `TradingSession`/`PaperBroker` fixture that asserts reads go through the accessor.

- [ ] **Step 3: Add canonical accessors and route reads**

In `base.py`, add read-only accessors that the live path already touches (verify existing readers first with `grep -rn "_quote\b" ntrade/`):
```python
@property
def market(self):
    """Canonical live market state (quote/depth) for this instrument."""
    return self._market_capability  # existing MarketCapability

@property
def history(self):
    """Canonical historical series accessor."""
    return self._history  # existing HistoricalSeries
```
Update `ntrade/kernel/trading_session.py`, `ntrade/scanners/builtin.py`, `ntrade/engines/*.py`, `ntrade/runner/live_runner.py` to read `instrument.market` / `instrument.history` instead of `instrument._quote` / `instrument._history`. (Do NOT touch `_stream` — feeds own it.)

- [ ] **Step 4: Delete the unintegrated facades**

In `capabilities.py`, remove `TradeCapability` (106-207), `AnalyticsCapability` (incl. the dead `rsi`/`atr`/`detect_absorption`), `StreamCapability`, `ExtensionCapability` and the `Instrument.trade`/`extension` properties in `base.py` that reference them. Keep `MarketCapability` and `DerivativesCapability` (the two real entry points). Update exports in `domain/instruments/__init__.py` and `ntrade/__init__.py`.

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/domain/instruments/ ntrade/kernel/trading_session.py ntrade/engines/ ntrade/scanners/
git commit -m "T-013 route reads through canonical accessors; retire unused capability facades"
```

---

### Task 9: Arm ScannerFacade M6 throttle on a built-in scanner (T-017)

**Files:**
- Modify: `ntrade/scanners/builtin.py` (set `rate_limit_seconds` on one scanner)
- Modify: `ntrade/domain/scanner.py` (fix `Scanner.top` to share `_run`'s rank logic — currently duplicates it)
- Test: `tests/test_scanner.py`

**Interfaces:**
- Consumes: `Scanner.rate_limit_seconds` (scanner.py:56-57), `ScannerFacade._run` cache (scanner.py:136-165).
- Produces: at least one built-in scanner armed with the M6 throttle, proving the mechanism; `Scanner.top` no longer a divergent copy.

- [ ] **Step 1: Write the failing test**

```python
def test_builtin_scanner_arms_m6_throttle():
    from ntrade.scanners.builtin import MomentumScanner, VolumeSpikeScanner, BreakoutScanner
    for cls in (MomentumScanner, VolumeSpikeScanner, BreakoutScanner):
        if hasattr(cls, "rate_limit_seconds"):
            assert cls.rate_limit_seconds > 0, f"{cls.__name__} must set a throttle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_scanner.py::test_builtin_scanner_arms_m6_throttle -q`
Expected: FAIL (all builtins keep `rate_limit_seconds = 0.0`).

- [ ] **Step 3: Arm one scanner + dedupe `Scanner.top`**

In `builtin.py`, set `MomentumScanner.rate_limit_seconds = 30.0` (scanning the full universe every tick is the M6 problem). In `scanner.py`, replace `Scanner.top` (64-70) to delegate to `ScannerFacade._run` ranking — or simply have callers use the facade; delete `top` and point its two test callers at the facade (`tests/test_scanner.py:120,149,162`).

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_scanner.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/scanners/builtin.py ntrade/domain/scanner.py tests/test_scanner.py
git commit -m "T-017 arm M6 scanner throttle; dedupe Scanner.top ranking"
```

---

### Task 10: StrategyRunner — complete or trim the documented surface (T-021)

**Files:**
- Modify: `ntrade/kernel/runner.py`
- Test: `tests/test_strategy_runner.py`

**Interfaces:**
- Consumes: `TradingKernel.start()/stop()`, strategy `enabled` flags.
- Produces: a `StrategyRunner` whose public surface is either fully wired into the kernel or trimmed to what production uses.

**Decision (recommended: trim):** the audit found `remove`/`enable`/`disable`/`running`/`names`/`strategy`/`risk`/`status`/`start`/`stop`/`__enter__/__exit__` are all test-only; `start`/`stop` just delegate to the kernel. `StrategyRunner` is a thin convenience over `TradingKernel` — the documented ARCHITECTURE.md surface is `add()` + lifecycle. Keep `add()` + a delegating `release()`; delete the test-only mutation surface (`remove`/`enable`/`disable`/`set_enabled`) and update `tests/test_strategy_runner.py` accordingly.

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_strategy_runner.py::test_runner_surface_is_minimal -q`
Expected: FAIL (`remove`/`enable`/`disable` present).

- [ ] **Step 3: Trim the runner surface**

In `runner.py`, delete `remove`, `enable`, `disable`, `set_enabled`, `_set_enabled` and any `running`/`status` property that only reads kernel state. Keep `add()`, `start()`/`stop()` (they delegate to the kernel — document them as such), and add `release()` = `remove` for teardown. Update `tests/test_strategy_runner.py` to drop tests for the deleted surface.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_strategy_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.
```bash
git add ntrade/kernel/runner.py tests/test_strategy_runner.py
git commit -m "T-021 trim StrategyRunner to the documented add/release surface"
```

---

## Definition of Done (whole batch)

- [ ] All ten tasks implemented with failing-test-first discipline.
- [ ] `./.venv/bin/python -m pytest -q` → 624+ passing, no regressions.
- [ ] Kanban cards T-012..T-021 moved to done (`kanban.py task status <id> done`; `kanban.py update` per task).
- [ ] Commit per task with the repo's message style.
- [ ] The DhanTransport/DhanMapper duplication from the audit is gone (Task 2), and every R-004/R-005/R-006 event has a consumer (Tasks 5-6).
