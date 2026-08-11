# Broker Rate Limit Infra — Parallel Multi-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-08-02. Branch: `integration-completeness`.
Source: principal review finding "Broker Rate Limit Infra" — kanban cards T-025, T-026, B-010, B-011, T-027 (all `planned`).

## Verdict

What we have today does **not** protect live trading. A single `RateLimiter(10/s)` on LTP only is both **too loose for Quote APIs** (Dhan: **1/s**) and **too coarse** for Order/Data/NonTrading buckets. Most outbound Tradehull calls bypass the limiter entirely. Treat current wiring (T-016) as a partial stub, not production infra.

| Invariant | Current | Required |
|-----------|---------|----------|
| Every REST call passes one choke point | Fail | Pass |
| Quota class matches Dhan table | Fail (single 10/s) | Pass |
| Quote ≤ 1/s | Fail (10/s) | Pass |
| Shared across instruments/threads | Partial (only LTP) | Pass |
| Rate-limit errors not retried as flaky | Fail | Pass |
| No silent None/empty on DH-904 | Fail (chain/history swallow) | Pass |
| Orders never bypass gate | Fail (`self.tsl` at dhan.py:219-333) | Pass |
| Clock injectable (tests / zero-parity) | Fail (`time.sleep` hardwired) | Pass |
| Paper broker unaffected | OK | OK |

## Verified facts (line-accurate)

- `RateLimiter(calls_per_second=10.0)` is built in `DhanBroker.connect()` (dhan.py:68) and passed as `rate_limiter=` to `DhanTransport` (dhan.py:69-70). Imported at dhan.py:34.
- `DhanTransport._throttled()` (dhan_transport.py:80-84) is the *only* limiter call site — inside `get_ltp`'s `_try_ltp` (dhan_transport.py:97). Every other `self._tsl.*` call in the transport is ungoverned.
- `RateLimiter` (retry.py:70-97) is a min-interval spacer (`_last_time` + `time.sleep` **under the lock**), NOT a token bucket despite the docstring — no burst tokens, no multi-window, hardwired `time.monotonic`/`time.sleep`.
- `RetryPolicy.execute` (retry.py:19-67) retries **any** exception — including rate-limit-shaped failures → amplifies DH-904.
- **Order-path bypass:** `DhanBroker` calls `self.tsl.*` directly for `place_super_order` (dhan.py:219), `order_placement` (235), `cancel_order` (256), `modify_order` (268), `get_order_status` (290), `get_order_detail` (311), `get_executed_price` (326), `get_executed_price_and_time` (333). These skip transport entirely. Transport already implements equivalent gated-able methods (dhan_transport.py:257-310).
- **Silent swallows:** `get_historical` returns empty `CandleSeries` on any exception (dhan_transport.py:139-143), `get_long_term_historical`/`get_daily_historical` return empty `DataFrame`, `get_expiry_list`/`get_ohlc` return `[]`/`{}`, `get_depth` returns `None` — a DH-904 looks like "no bars" to strategies (ERROR-016 pattern).
- `dhan_feed.py:124` has its own `RateLimiter(0.5/s)` for reconnect — websocket feed is out of scope (finding keeps it separate).
- Existing test `tests/test_retry.py::test_transport_rate_limiter_spaces_calls` asserts the 10/s limiter path — must be updated when the limiter is dropped.
- Test suite baseline: **663 passing** (`./.venv/bin/python -m pytest -q`).

## Official Dhan quotas (dhanhq.co/docs/v2 — Rate Limit table)

| Class | /sec | /min | /hour | /day |
|-------|------|------|-------|------|
| Order APIs | 10 | 250 | 1000 | 7000 |
| Data APIs | 5 | — | — | 100000 |
| Quote APIs | **1** | Unlimited | Unlimited | Unlimited |
| Non Trading APIs | 20 | Unlimited | Unlimited | Unlimited |

## Parallel execution model (multi-agent team)

Five tasks, three waves. **File ownership is exclusive per agent** so agents never edit the same file concurrently. The frozen interface contract in the next section lets agents code in parallel against agreed signatures.

| Wave | Agent | Task | Kanban | Owns (files) | Depends on |
|------|-------|------|--------|--------------|------------|
| 1 | A | Add `BrokerRateGate` + `Quota` + `RateLimited` | T-025 | `ntrade/execution/rate_limit.py` (new), `tests/test_rate_limit.py` (new) | — |
| 1 | B | Split retry: never retry DH-904 | B-011 | `ntrade/execution/retry.py`, `ntrade/execution/__init__.py`, `tests/test_retry.py` | T-025 (imports contract module; merge T-025 first) |
| 2 | C | Transport choke point | T-026 | `ntrade/brokers/dhan_transport.py`, `tests/test_dhan_transport.py` (new) | T-025; removes one obsolete test from `tests/test_retry.py` *after* B-011 merges (waves are sequential) |
| 2 | D | Kill `self.tsl` bypass + gate in `connect()` | B-010 | `ntrade/brokers/dhan.py`, `tests/test_dhan_broker.py` | T-026 |
| 3 | E | Integration checks | T-027 | `tests/test_rate_gate_integration.py` (new) | Waves 1-2 merged |

**Merge order (sequential waves):** T-025 → B-011 → T-026 → B-010 → T-027. **Parallelism is within a wave only:** Wave 1 agents A+B (T-025 vs B-011) and Wave 2 agents C+D (T-026 vs B-010) work simultaneously on disjoint files. Cross-wave file sharing is safe by construction (wave N+1 starts only after wave N merges) — `tests/test_retry.py` is B-011's file; T-026's one-line removal there lands after B-011 with no contention.

## Frozen interface contract (write code against this, do not renegotiate)

```python
# ntrade/execution/rate_limit.py  (new module)

class Quota(Enum):
    QUOTE = "quote"            # 1/s
    DATA = "data"              # 5/s, 100_000/day
    ORDER = "order"            # 10/s, 250/min, 1000/h, 7000/day
    NON_TRADING = "non_trading"  # 20/s

class RateLimited(RuntimeError):
    """Raised when Dhan rejects a call with DH-904 / Rate_Limit."""
    def __init__(self, quota: Quota, retry_after: float | None = None,
                 message: str = ""): ...

class BrokerRateGate:
    def __init__(self, clock=None, sleep=None, windows: dict | None = None): ...
    def acquire(self, quota: Quota) -> None:
        """Block until every window for quota has capacity. Sleep OUTSIDE the lock."""
    def penalize(self, quota: Quota, seconds: float) -> None:
        """Back off quota for `seconds` after a DH-904."""

def is_rate_limited(exc: Exception) -> bool:
    """True when exc text matches DH-904 / Rate_Limit / 'rate limit' / HTTP 429."""
```

- Window math: sliding/fixed windows per class (deque of timestamps + a day counter), **not** the min-interval hack. `acquire` computes the wait, releases the brief lock, sleeps, re-checks.
- `clock` (callable → float seconds) and `sleep` (callable) are injected; defaults are `time.monotonic`/`time.sleep`. Tests pass fake clock/sleep → deterministic, zero wall-clock.
- `RetryPolicy` gains `no_retry_on: Callable[[Exception], bool] | None = None`. Default: **never** retry `RateLimited` / `is_rate_limited(exc)` matches. `get_ltp`'s narrow retry keeps retrying only `ValueError("LTP is 0")`.
- `DhanTransport.__init__` signature is **frozen**: `DhanTransport(tsl, retry_policy: RetryPolicy | None = None, gate: BrokerRateGate | None = None, clock=None)` — the `rate_limiter` param is **deleted** (replaced by `gate`). Both wave-2 agents (T-026 owns the constructor change, B-010 writes `connect()` against `gate=`) code to this exact signature.
- Thin re-export: `from ntrade.execution.retry import RateLimited, Quota, BrokerRateGate, is_rate_limited` (keeps old `from ntrade.execution.retry import RateLimiter, RetryPolicy` imports working — `RateLimiter` stays for dhan_feed).

---

## Task 1 (T-025): Add BrokerRateGate + Quota + RateLimited

**Files:** `ntrade/execution/rate_limit.py` (new), `tests/test_rate_limit.py` (new).

**Interfaces:** Produces `Quota`, `RateLimited`, `BrokerRateGate`, `is_rate_limited` per the frozen contract. Consumes nothing.

**Context:** New infrastructure module. Dhan default windows from the quota table. Must be stdlib-only, thread-safe, clock/sleep injectable.

- [ ] **Step 1: Write the failing tests** (`tests/test_rate_limit.py`):
  - `test_quote_cannot_fire_twice_within_1s` — fake clock: acquire(QUOTE) at t=0, acquire(QUOTE) at t=0.5 must block until t≥1.0 (assert the sleep was called with ≥0.5).
  - `test_data_window_5_per_second` — five DATA acquires at t=0 pass; a 6th at t=0.5 blocks.
  - `test_order_minute_hour_day_windows` — 250/min and 1000/h enforcement via fake clock fast-forward.
  - `test_penalize_backs_off_quota` — `penalize(QUOTE, 5.0)` makes the next acquire block ≥5s even if the 1/s window is clear.
  - `test_shared_gate_serializes_threads` — N threads × M acquires with a real gate; assert total wall time ≥ (N*M − 1)/rate (loose bound).
  - `test_injectable_clock_and_sleep` — fake clock/sleep are used; `time.sleep` never called.
  - `test_is_rate_limited_matches_dh904` — `is_rate_limited(RateLimited(Quota.QUOTE))` and a `RuntimeError("...DH-904...")`/`429` both True; a `ValueError` False.
- [ ] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_rate_limit.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement `ntrade/execution/rate_limit.py`** per the frozen contract. Fixed windows: `QUOTE=(1/s)`, `DATA=(5/s, 100_000/day)`, `ORDER=(10/s, 250/min, 1000/h, 7000/day)`, `NON_TRADING=(20/s)`. `acquire` computes the longest wait across windows, releases the lock, sleeps, re-checks. `penalize` appends a cooldown marker to the class's window deque. All `time.*` calls go through `self._clock`/`self._sleep`.
- [ ] **Step 4: Run tests to verify they pass** — same command → PASS.
- [ ] **Step 5: Commit** (stage only the two files):
```bash
git add ntrade/execution/rate_limit.py tests/test_rate_limit.py
git commit -m "T-025 add BrokerRateGate multi-window rate gate infra"
```

---

## Task 2 (B-011): Stop RetryPolicy retrying DH-904; raise RateLimited instead of empty/None

**Files:** `ntrade/execution/retry.py`, `ntrade/execution/__init__.py`, `tests/test_retry.py`.

**Interfaces:** Consumes `RateLimited`/`is_rate_limited` from `ntrade.execution.rate_limit`. Produces a `RetryPolicy` that never retries rate-limit failures.

**Context:** `RetryPolicy.execute` retries any exception (retry.py:19-67). After a DH-904 the retry storm burns more quota and widens the outage. The "raise instead of empty/None" half of this card lands inside Task 3's transport work (same file as the choke point) — this card owns the policy split + re-exports.

- [ ] **Step 1: Write the failing tests** (`tests/test_retry.py`):
  - `test_retry_policy_does_not_retry_rate_limited` — fn raises `RateLimited(Quota.QUOTE)` on attempt 1; `RetryPolicy(max_retries=3).execute(fn)` must raise immediately with exactly **1** fn call.
  - `test_retry_policy_retries_transient_errors` — fn raises `ConnectionError` twice then succeeds; execute returns the value (3 calls).
  - `test_rate_limited_reexported` — `from ntrade.execution.retry import RateLimited, Quota, BrokerRateGate` works.
- [ ] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_retry.py -k "rate_limited or reexported" -q` → FAIL.
- [ ] **Step 3: Implement** — add `no_retry_on` to `RetryPolicy` (default: `lambda exc: is_rate_limited(exc)`), check it inside `execute` before deciding to retry; add the thin re-exports to `retry.py` and `ntrade/execution/__init__.py`.
- [ ] **Step 4: Run tests to verify they pass** — same command → PASS.
- [ ] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 663+.
```bash
git add ntrade/execution/retry.py ntrade/execution/__init__.py tests/test_retry.py
git commit -m "B-011 stop retrying DH-904 rate-limit failures"
```

---

## Task 3 (T-026): Route every DhanTransport _tsl call through _invoke(quota, fn)

**Files:** `ntrade/brokers/dhan_transport.py`, `tests/test_dhan_transport.py` (new) or additions to `tests/test_retry.py`.

**Interfaces:** Consumes `BrokerRateGate`/`Quota`/`RateLimited` from `ntrade.execution.rate_limit`. Produces `DhanTransport._invoke(quota, fn, *, retryable=False)` as the single choke point.

**Context:** Every `self._tsl.*` call in dhan_transport.py is classified and wrapped. `_throttled()` + `rate_limiter` param are dropped (DhanTransport no longer takes `rate_limiter=`; takes `gate=` instead). The silent empty/None swallows on history/chain/OHLC (dhan_transport.py:139-143, 188-198, 212-231, 283-287, 291-295) must raise `RateLimited` when `is_rate_limited(exc)` — never return empty as success. Note: transport `__init__` signature change ripples to dhan.py (Task 4 owns that call site) and to test constructors (`test_retry.py::test_transport_rate_limiter_spaces_calls` is updated/removed here).

**Note — `get_quote` consumes two QUOTE tokens:** it calls `get_ltp_data` (inside `get_ltp`) **and** `get_quote_data`, so one `get_quote` = 2 acquires at 1/s → effective quote rate **0.5 quotes/s**. This is correct behavior (Dhan counts both endpoints against Quote), just document it — do not "optimize" the gate to pretend one quote call is one token.

**Classification map (fixed):**
- QUOTE: `get_ltp_data`, `get_quote_data`, `get_ohlc_data`
- DATA: `get_historical_data`, `get_long_term_historical_data`, `get_option_chain`, `full_market_depth_data`/`get_market_depth_df`
- ORDER: `order_placement`, `place_super_order`, `cancel_order`, `modify_order`, `get_order_status`, `get_order_detail`, `get_executed_price`, `get_executed_price_and_time`
- NON_TRADING: `get_balance`, `get_positions`, `get_holdings`, `get_orderbook`, `get_trade_book`, `order_report`, `get_live_pnl`, `get_expiry_list`, `get_expiry_date`, `get_future_script`, `get_lot_size`, `get_start_date`, `get_instrument_file`, `instrument_df`, `get_instrument_metadata`, `blocks_day`

  (`instrument_df` is a **property**, not a method — wrap as `_invoke(Quota.NON_TRADING, lambda: self._tsl.instrument_df)`.)

- [ ] **Step 1: Write the failing tests** (`tests/test_dhan_transport.py`):
  - `test_transport_invokes_gate_for_quote` — mock tsl + fake-clock gate; `get_ltp("TCS")` twice at t=0 blocks the 2nd until t≥1 (gate acquire called with `Quota.QUOTE`).
  - `test_history_raises_rate_limited_on_dh904` — `tsl.get_historical_data` raises `RuntimeError("DH-904")`; `get_historical(...)` raises `RateLimited` (not empty `CandleSeries`).
  - `test_ohlc_raises_rate_limited_not_empty_dict` — `tsl.get_ohlc_data` raises `RateLimited`; `get_ohlc` propagates.
  - `test_order_path_acquires_order_quota` — `place_order` → gate.acquire called with `Quota.ORDER`.
- [ ] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_dhan_transport.py -q` → FAIL (no `_invoke`, no gate param).
- [ ] **Step 3: Implement** — add `_invoke(quota, fn, *, retryable=False)` (acquire → fn → on `RateLimited`: `gate.penalize` + re-raise); replace `_throttled` with `_invoke(Quota.QUOTE, ...)`; wrap every `self._tsl.*` call with its class; delete `rate_limiter`/`_throttled`; change `__init__` to `gate: BrokerRateGate | None = None` (None ⇒ unthrottled, paper-safe); make history/chain/OHLC error paths raise `RateLimited` on `is_rate_limited(exc)`. Update/remove `test_transport_rate_limiter_spaces_calls` (it asserts the deleted limiter).
- [ ] **Step 4: Run tests to verify they pass** — same command → PASS.
- [ ] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 663+ (fix any test constructing `DhanTransport(rate_limiter=...)`).
```bash
git add ntrade/brokers/dhan_transport.py tests/test_dhan_transport.py tests/test_retry.py
git commit -m "T-026 choke every DhanTransport call through BrokerRateGate"
```

---

## Task 4 (B-010): Move DhanBroker order/status paths onto throttled transport; drop 10/s limiter

**Files:** `ntrade/brokers/dhan.py`, `tests/test_dhan_broker.py`.

**Interfaces:** Consumes `BrokerRateGate`/`Quota` from `ntrade.execution.rate_limit` and the gated transport from Task 3. Produces a broker with **zero** `self.tsl.*` order-path call sites and a gate owned per session.

**Context:** `connect()` builds `RateLimiter(10/s)` (dhan.py:68) — replace with a shared `BrokerRateGate()` (quote defaults: 1/s) passed to the transport as `gate=`. Route the 8 order/status `self.tsl.*` call sites (dhan.py:219, 235, 256, 268, 290, 311, 326, 333) through the existing transport methods (`place_order`, `place_super_order`, `cancel_order`, `modify_order`, `get_order_status`, `get_order_detail`, `get_executed_price`, `get_executed_price_and_time`). Capability functions that call `broker.tsl.*` (advanced orders, kill switch, etc.) are **out of scope** for this card (finding: order/status path only); leave them.

**Ripples that MUST land in this card:**
- Delete the now-dead `from ntrade.execution.retry import RateLimiter` import (dhan.py:34) once the limiter is gone.
- Keep the zero-parity clock wiring: `DhanTransport(self.tsl, gate=self._gate, clock=getattr(self, "_clock", None))` — do not drop `clock=` in the rewrite.
- `get_order_status`/`get_order_detail` currently swallow every exception (`except Exception: return order` / `return {}`, dhan.py:290-307). After routing through transport, **`RateLimited` must NOT be swallowed** — let it propagate so DH-904 on order-status polling is visible (finding: "no silent swallow; surface rejection"). Keep the defensive swallows for other exceptions (D-016 stale-order contract).

- [ ] **Step 1: Write the failing tests** (`tests/test_dhan_broker.py`):
  - `test_connect_uses_broker_rate_gate` — broker with mock auth; after `connect()`, `broker._transport._gate` is a `BrokerRateGate` and `broker._rate_limiter` attribute is gone (no 10/s limiter).
  - `test_place_order_routes_through_transport` — mock `_transport.place_order` returns "O1"; `broker.place_order(order)` calls `_transport.place_order`, sets `order_id="O1"`.
  - `test_cancel_and_status_route_through_transport` — `cancel_order`/`get_order_status` call `_transport.cancel_order`/`get_order_status`.
  - `test_no_self_tsl_order_calls_remain` — grep-level assertion: no `self.tsl.order_placement|place_super_order|cancel_order|modify_order|get_order_status|get_order_detail|get_executed_price` in dhan.py order methods.
- [ ] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_dhan_broker.py -k "routes_through_transport or broker_rate_gate or self_tsl_order" -q` → FAIL.
- [ ] **Step 3: Implement** — in `connect()`: `self._gate = BrokerRateGate()`; `DhanTransport(self.tsl, gate=self._gate, clock=...)`; delete `_rate_limiter`. Replace each order/status call body with the transport call, preserving SEBI F&O MARKET→LIMIT conversion and BRACKET → `place_super_order` logic exactly.
- [ ] **Step 4: Run tests to verify they pass** — same command → PASS.
- [ ] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 663+.
```bash
git add ntrade/brokers/dhan.py tests/test_dhan_broker.py
git commit -m "B-010 route order paths through throttled transport; drop 10/s limiter"
```

---

## Task 5 (T-027): Integration checks

**Files:** `tests/test_rate_gate_integration.py` (new).

**Interfaces:** Consumes the full stack from Tasks 1-4.

**Context:** Prove end-to-end behavior the unit tests only imply. All fake-clock; never hit Dhan.

- [ ] **Step 1: Write the integration tests**:
  - `test_quote_class_spaced_at_1_per_second` — two `get_quote` calls through a real broker-with-mock-tsl + fake-clock gate: each `get_quote` consumes 2 QUOTE tokens (ltp + quote_data), so two back-to-back calls block the second until ≥2s total spacing.
  - `test_shared_gate_across_instruments_and_threads` — one gate shared by transport; two threads doing LTP+history concurrently never exceed their class windows (loose wall-clock bound).
  - `test_order_path_pays_order_quota_and_propagates_rejection` — `place_order` → gate ORDER window consumed; a DH-904 on placement surfaces as `RateLimited` (no silent reject→PENDING).
  - `test_penalize_on_dh904_backs_off_class` — history raises DH-904 → `RateLimited`; subsequent QUOTE calls still proceed (class-scoped backoff), subsequent DATA call blocked for `retry_after`.
  - `test_paper_broker_unaffected` — PaperBroker path has no gate and no new failure modes.
- [ ] **Step 2: Run tests to verify they pass** — `./.venv/bin/python -m pytest tests/test_rate_gate_integration.py -q` → PASS (they should be green on merged waves 1-2; write them failing-first only if a behavior is missing).
- [ ] **Step 3: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 663+.
```bash
git add tests/test_rate_gate_integration.py
git commit -m "T-027 integration tests for broker rate gate"
```

---

## Definition of Done (whole batch)

- [ ] Cards T-025/B-011/T-026/B-010 implemented failing-test-first; T-027 written as the verification wave (tests may be authored after behavior lands, then run green); `./.venv/bin/python -m pytest -q` → 663+ passing, no regressions.
- [ ] Every Dhan REST call passes `DhanTransport._invoke(quota, ...)`; zero `self.tsl.*` call sites in dhan.py's order/status methods.
- [ ] Quote class defaults to 1/s; DH-904 raises `RateLimited` (never empty/None success); `RetryPolicy` never retries rate limits.
- [ ] One `BrokerRateGate` per broker session, shared across threads/instruments; clock/sleep injectable; PaperBroker unaffected.
- [ ] Kanban cards T-025, T-026, B-010, B-011, T-027 moved to done (`kanban.py task status <id> done`; `kanban.py update` per card).

## Out of scope (explicitly not in this change)

- Scanner `rate_limit_seconds` (app-level cache, unrelated to broker quotas).
- Websocket feed quota (only reconnect gate, already 0.5/s).
- Batch-history API (separate product question).
- Capability-surface `broker.tsl.*` advanced orders — **now tracked as follow-up cards T-028..T-032** (see next section).

---

## Follow-up cards (T-028..T-032) — capability-surface bypass sweep

**Sweep result (2026-08-02):** all remaining uncovered `broker.tsl.*` call sites live in `ntrade/brokers/dhan.py` capability functions (lines 490-754). `ntrade/brokers/capabilities.py` is pure registry/facade infra (73 lines, zero `tsl` references). No other `tsl.*` call sites exist repo-wide (`grep -rn "\.tsl\." ntrade/ scripts/` outside dhan.py → none). The 8 order/status sites (dhan.py:219-333) are already covered by B-010; nothing here overlaps it.

| Capability (dhan.py line) | TSL method | Quota class | Card |
|---|---|---|---|
| `place_super_order` (579) | `place_super_order` | ORDER | T-028 |
| `place_slice_order` (594) | `place_slice_order` | ORDER | T-028 |
| `place_forever_order` (629) | `place_forever_order` | ORDER | T-028 |
| `place_conditional_trigger` (612) | `place_conditional_trigger` | ORDER | T-028 |
| `cancel_all_orders` (642) | `cancel_all_orders` | ORDER | T-028 |
| `get_super_orders` (649) | `get_super_orders` | ORDER | T-029 |
| `modify_super_order` (658) | `modify_super_order` | ORDER | T-029 |
| `cancel_super_order` (669) | `cancel_super_order` | ORDER | T-029 |
| `get_forever_orders` (676) | `get_forever_orders` | ORDER | T-029 |
| `modify_forever_order` (685) | `modify_forever_order` | ORDER | T-029 |
| `cancel_forever_order` (696) | `cancel_forever_order` | ORDER | T-029 |
| `get_exchange_time` (754) | `get_exchange_time` | ORDER | T-029 |
| `get_conditional_triggers` (702) | `get_all_conditional_triggers` | ORDER | T-030 |
| `get_conditional_trigger` (708) | `get_conditional_trigger_by_id` | ORDER | T-030 |
| `delete_conditional_trigger` (714) | `delete_conditional_trigger` | ORDER | T-030 |
| `kill_switch` (503) | `kill_switch` | NON_TRADING | T-031 |
| `enable_pnl_based_exit` (512) | `enable_pnl_based_exit` | NON_TRADING | T-031 |
| `margin_calculator` (490) | `margin_calculator` | NON_TRADING | T-031 |
| `atm_strike` (721) | `ATM_Strike_Selection` | DATA | T-032 |
| `itm_strike` (727) | `ITM_Strike_Selection` | DATA | T-032 |
| `otm_strike` (733) | `OTM_Strike_Selection` | DATA | T-032 |
| `expired_option_data` (742) | `get_expired_option_data` | DATA | T-032 |

**22 sites, 0 in QUOTE class** (capability surface has no raw quote reads — those route through broker/transport already). **Recommended execution:** after T-025/T-026 land, route each capability's `broker.tsl.*` call through `broker._transport._invoke(<class>, ...)` (or a thin `broker._gated(quota, fn)` helper). **Only 3 of the 15 ORDER sites have transport twins** (`place_super_order`, `cancel_order`, `modify_order` at dhan_transport.py:257-310 — and even those are best routed via `_invoke(Quota.ORDER, ...)` for uniformity); the other 19 sites (`place_slice_order`, `place_conditional_trigger`, `place_forever_order`, `cancel_all_orders`, `get/modify/cancel_super_order`, `get/modify/cancel_forever_order`, `get_exchange_time`, conditional-trigger lifecycle, `kill_switch`, `enable_pnl_based_exit`, `margin_calculator`, `ATM/ITM/OTM_Strike_Selection`, `get_expired_option_data`) have **no** transport method — wrap the raw TSL call with `_invoke(<class>, lambda: broker.tsl.<method>(...))`. Wire the gate in `connect()` (B-010) so capabilities inherit it for free.

**Out of scope for follow-ups too:** `depth20`/`expiry_list`/`lot_size`/`future_script`/`long_term_history`/`ohlc`/`start_date`/`instrument_file` capabilities already delegate to broker→transport (covered by T-026's classification map) — verified, not bypass sites.
