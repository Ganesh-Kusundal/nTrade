# Review Findings Sweep (K-020..K-026) — Parallel Multi-Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

Date: 2026-08-03. Branch: `integration-completeness`.
Source: parallel principal-engineer + quant-trading review sweep (5 code-review agents across broker/execution/kernel/domain/runner subsystems, hypotheses verified against source) — kanban cards K-020..K-026 (all `backlog`).

## Verdict

The rate-limit infrastructure batch (T-025..T-032, B-010/B-011) landed and is **verified complete** — but the sweep found **7 residual gaps** the batch did not cover. One is a P1 live-correctness bug; five are P2 debt/risk; one is P3 cleanup. None are speculative: every card below was confirmed against current source before registration (the other ~20 reviewer hypotheses — SymbolMaster race, moneyness flip, IV solver, DH-904 retry, `self.tsl` order bypass, gate zero-cash guard — were **falsified** as already-hardened and are NOT cards).

| Invariant | Current | Required |
|-----------|---------|----------|
| Rate-limited execution-price poll never looks like a 0.0 fill | Fail (K-020) | Pass |
| DH-904 on broker data reads never looks like "no data" | Fail (K-021) | Pass |
| All 5 scanners throttle symmetrically | Fail (K-022) | Pass |
| Scanner spike ratio unit-consistent live | Fail (K-023) | Pass |
| Order product type derived from instrument kind | Fail (K-024) | Pass |
| Resample label convention matches CandleEngine bucketing | Verify (K-025) | Pass |
| gate._equity_trace clean + docstring truthful | Fail (K-026) | Pass |

## Verified facts (line-accurate)

- **K-020:** `DhanBroker.get_executed_price` (dhan.py:386-393) and `get_executed_price_and_time` (dhan.py:395-403) wrap transport calls in `try/except Exception: return float(order.avg_price or 0.0)` / `(avg_price, "")`. `get_order_status` (dhan.py:338-363) and `get_order_detail` (dhan.py:365-376) were fixed to re-raise `RateLimited` first (dhan.py:346-347, 373-374) — these two were missed. Transport `get_executed_price` (dhan_transport.py:483) routes through `_invoke(Quota.ORDER, ...)` which raises `RateLimited` — the broker swallows it to 0.0.
- **K-021:** Broker convenience methods wrap raw `self._tsl.*` (or transport) in `except Exception` → empty/None: `get_orderbook`→`OrderBook()` (dhan.py:411-412), `get_trade_book`→`TradeBook()` (420-421), `order_report`→`[]` (453-454), `get_expiry_list`→`[]` (462-463), `get_expiry_date`→`[]` (474-475), `get_long_term_historical`→`{}` (505-506), `get_start_date`→`None` (511-512), `get_instrument_file`→`None` (517-518), `get_instrument_metadata`→`{}` (531-532). The transport choke point (T-026) normalises DH-904→`RateLimited`; these broker wrappers then swallow it — a DH-904 on expiry list looks like "no expiries" (ERROR-016 class).
- **K-022:** `MomentumScanner`/`VolumeSpikeScanner`/`BreakoutScanner` set `rate_limit_seconds = 30.0` (builtin.py:74, 100, 132); `GapScanner` and `ImbalanceScanner` (builtin.py:22, 153) have no `rate_limit_seconds` (defaults 0.0). `ScannerFacade` rate-limits per scanner id (M6/T-017); gap/imbalance scan the full universe every cycle.
- **K-023:** `VolumeSpikeScanner.scan` (builtin.py:76-100) computes `ratio = vol / avg_vol` from `inst.market.volume()` and `avg_volume` indicator. Docstring (builtin.py:82-85) admits live `quote.volume` is day-cumulative while `avg_volume` is per-candle — ratio path meaningless live; only the absolute `min_volume` fallback is reliable. Documented, not guarded.
- **K-024:** `OrderFacade.buy/sell/limit/market/stop/cover/bracket` (order.py:125-157) default `trade_type=TradeType.MIS` regardless of `instrument` kind. `Order.trade_type` default `MIS` (order.py:49). Equity delivery requires CNC; MIS on equity = intraday margin order — silent wrong product type when the caller omits `trade_type`.
- **K-025:** `CandleEngine._bucket` (candle_engine.py:30-34) floors naive→UTC-pinned epoch and labels closed candles at `bucket + seconds` (candle_engine.py:67). `HistoricalSeries.resample` (history.py:103-111) uses pandas `indexed.resample(rule).agg(...)` with pandas defaults (`closed='left'`, `label='left'`) — a different label convention than the engine's end-of-bar labels. F-001 sub-5m path depends on this.
- **K-026:** `_equity_trace` (gate.py:26-30) writes `positions[e.symbol] = (q, ltp)` then immediately `positions.pop(e.symbol, None)` when `q == 0` — redundant write-then-pop. Docstring drift: says "yields (peak, eq) on each settled BalanceChangedEvent" which is accurate, but the D-017 review noted the wording "after each state change" vs balance-event semantics; keep the docstring honest.

## Parallel execution model (multi-agent team)

Seven cards, four waves. **File ownership is exclusive per agent** so agents never edit the same file concurrently. The frozen interface contract below lets agents code in parallel against agreed signatures.

| Wave | Agent | Task | Kanban | Owns (files) | Depends on |
|------|-------|------|--------|--------------|------------|
| 1 | A | Broker boundary `RateLimited` propagation: K-020 then K-021 (same file, sequenced by one agent) | K-020, K-021 | `ntrade/brokers/dhan.py`, `tests/test_gap_closure.py` (add), `tests/test_dhan_broker.py` (add) | — |
| 2 | C | Throttle Gap/Imbalance scanners symmetrically | K-022 | `ntrade/scanners/builtin.py`, `tests/test_scanner.py` | — |
| 2 | D | Guard VolumeSpike ratio branch in live mode | K-023 | `ntrade/scanners/builtin.py`, `tests/test_scanner.py` | — |
| 3 | E | Derive OrderFacade trade_type default from instrument kind | K-024 | `ntrade/domain/orders/order.py`, `tests/test_orders.py`, `tests/test_domain_types.py` | — |
| 3 | F | Align/verify resample label vs CandleEngine bucketing + parity test | K-025 | `ntrade/domain/market/history.py`, `ntrade/engines/candle_engine.py`, `tests/test_history_stream.py`, `tests/test_candle_timezone.py` | — |
| 4 | G | gate._equity_trace cleanup + docstring fix | K-026 | `ntrade/runner/gate.py`, `tests/test_paper_gate.py` | — |

**Merge order (sequential waves):** (K-020 → K-021) → (K-022 ∥ K-023) → (K-024 ∥ K-025) → K-026. **Parallelism is within a wave only.**

**Same-file contention (explicit):** K-020 and K-021 both touch `ntrade/brokers/dhan.py`, so they are **merged into one Wave-1 agent (A) that implements them sequentially** — K-020 first (surgical `except RateLimited: raise` on the two executed-price methods), commit, then K-021 on the updated file, commit. Waves 2-4 files are disjoint from dhan.py and from each other, so no cross-wave contention exists.

## Frozen interface contract (write code against this, do not renegotiate)

- **K-020:** `DhanBroker.get_executed_price` and `get_executed_price_and_time` gain a `try/except RateLimited: raise` clause **before** the `except Exception` fallback (identical to get_order_status/get_order_detail at dhan.py:346-347, 373-374). Non-rate exceptions keep the existing degrade-to-avg_price/0.0 behavior (D-016 stale-order contract). No signature change.
- **K-021:** Broker data-read methods re-raise `RateLimited` when the underlying transport/tsl call raised it (check `is_rate_limited(exc)` from `ntrade.execution.rate_limit` if the call isn't already routing through `_invoke`). Full method list (11, not 9 — `get_future_script` and `get_lot_size` were added after review): `get_orderbook`, `get_trade_book`, `order_report`, `get_expiry_list`, `get_expiry_date`, `get_long_term_historical`, `get_start_date`, `get_instrument_file`, `get_instrument_metadata`, **`get_future_script` (→`None`, dhan.py:482)**, **`get_lot_size` (→`0`, dhan.py:489 — a 0 lot size can divide-by-zero downstream)**. All other exceptions keep the existing empty/None degrade. No signature change; `RateLimited` import added to dhan.py if not present.
- **K-022:** `GapScanner` and `ImbalanceScanner` gain `rate_limit_seconds = 30.0` class attributes (matching the other three scanners). No other change.
- **K-023:** `VolumeSpikeScanner.scan` keeps the ratio path only when the volume unit is comparable (backtest/replay where bars are per-candle). In live mode (`session.kernel.ctx.mode == "live"`), skip the `avg_volume` ratio branch and use the absolute `min_volume` fallback. Implement as a mode check at the top of the volume logic; do not change `ScannerResult` schema.
- **K-024:** `OrderFacade` default `trade_type` becomes instrument-kind-aware: `Equity`/`ETF`/`Spot` → `TradeType.CNC`, else (Future/Option/Index/others) → `TradeType.MIS`. Implement as a private helper `_default_trade_type(instrument)` in order.py. Explicit caller-passed `trade_type` always wins. Update `tests/test_orders.py` fixtures that rely on the MIS default for equities if any.
- **K-025:** First **verify** (write a parity test) whether `HistoricalSeries.resample` labels end-of-bar like CandleEngine. CandleEngine `_bucket` uses **floor-division epoch bucketing = closed-left bin membership**; only the *label* differs (engine labels at `bucket + seconds` = right edge; pandas default labels at bin start). If misaligned, set **`label="right"` ONLY, keeping `closed="left"`** — changing `closed` would change bin membership and break parity worse (add `origin`/`offset` only if the label still doesn't match the engine's end-of-bar label). Keep the `dropna(subset=["open"])` partial-bar drop. Verify the resample index is timezone-naive like the engine treats it (history.py `set_index("timestamp")`). Add the parity test in `tests/test_candle_timezone.py` or `tests/test_history_stream.py` asserting a 1m→5m resample labels the same bar boundaries as the engine's buckets for the same timestamps.
- **K-026:** Replace the write-then-pop with `if e.quantity: positions[e.symbol] = (e.quantity, e.ltp)`; verify the docstring states the balance-event yield semantics accurately (it currently does — keep it truthful after the edit).

## Task 1 (K-020): Re-raise RateLimited in executed-price path

**Files:** `ntrade/brokers/dhan.py`, `tests/test_gap_closure.py` (add test).

- [x] **Step 1: Write the failing test** in `tests/test_gap_closure.py`:
  - `test_executed_price_propagates_rate_limited` — broker with mock transport whose `get_executed_price` raises `RateLimited(Quota.ORDER)`; `order.executed_price()` must raise `RateLimited` (not return 0.0).
  - `test_executed_price_and_time_propagates_rate_limited` — same for `executed_price_and_time()`.
  - `test_executed_price_degrades_on_other_errors` — transport raises `ConnectionError`; `executed_price()` returns `avg_price` fallback (existing contract preserved).
- [x] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_gap_closure.py -k "executed_price" -q` → FAIL.
- [x] **Step 3: Implement** — add `except RateLimited: raise` before the generic `except Exception` in both methods (mirror dhan.py:346-347). Import `RateLimited` in dhan.py (already imported per K-021 contract check).
- [x] **Step 4: Run tests to verify they pass** — same command → PASS.
- [x] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 719+ passing.
```bash
git add ntrade/brokers/dhan.py tests/test_gap_closure.py
git commit -m "K-020 re-raise RateLimited in executed-price path (no silent 0.0)"
```

## Task 2 (K-021): Broker data reads propagate RateLimited

**Files:** `ntrade/brokers/dhan.py`, `tests/test_dhan_broker.py` (add tests).

**Runs after K-020 merges (same file, sequential).**

- [x] **Step 1: Write the failing tests** (`tests/test_dhan_broker.py`):
  - `test_expiry_list_propagates_rate_limited` — mock tsl `get_expiry_list` raises `RuntimeError("DH-904")` (or transport raises `RateLimited`); `broker.get_expiry_list(...)` raises `RateLimited`.
  - `test_orderbook_propagates_rate_limited` — same for `get_orderbook`.
  - `test_orderbook_still_degrades_on_other_errors` — non-rate exception → `OrderBook()` (existing degrade preserved).
- [x] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_dhan_broker.py -k "rate_limited" -q` → FAIL.
- [x] **Step 3: Implement** — for each data-read method: if the call already routes through transport `_invoke` (which raises `RateLimited`), add `except RateLimited: raise` before the generic swallow; if it calls `self._tsl.*` directly, wrap with `broker._gated(Quota.X, ...)` or `is_rate_limited(exc)` check + re-raise. Keep all other-exception degrade paths intact.
- [x] **Step 4: Run tests to verify they pass** — same command → PASS.
- [x] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 719+.
```bash
git add ntrade/brokers/dhan.py tests/test_dhan_broker.py
git commit -m "K-021 propagate RateLimited from broker data reads (no silent empty)"
```

## Task 3 (K-022): Throttle Gap/Imbalance scanners

**Files:** `ntrade/scanners/builtin.py`, `tests/test_scanner.py`.

- [x] **Step 1: Write the failing test** (`tests/test_scanner.py`): `test_gap_imbalance_throttled` — assert `GapScanner.rate_limit_seconds == 30.0` and `ImbalanceScanner.rate_limit_seconds == 30.0`.
- [x] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_scanner.py -k "throttled" -q` → FAIL (0.0 default).
- [x] **Step 3: Implement** — add `rate_limit_seconds = 30.0` to `GapScanner` and `ImbalanceScanner`.
- [x] **Step 4: Run tests to verify they pass** — same command → PASS.
- [x] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 719+.
```bash
git add ntrade/scanners/builtin.py tests/test_scanner.py
git commit -m "K-022 throttle gap/imbalance scanners symmetrically"
```

## Task 4 (K-023): Guard VolumeSpike ratio in live mode

**Files:** `ntrade/scanners/builtin.py`, `tests/test_scanner.py`.

- [x] **Step 1: Write the failing test** (`tests/test_scanner.py`): `test_volume_spike_live_uses_min_volume` — live-mode session (or a fake that reports `mode == "live"`), instrument with `avg_volume` indicator set; assert the ratio branch is skipped and `min_volume` fallback applies.
- [x] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_scanner.py -k "live_uses_min_volume" -q` → FAIL (ratio path still fires).
- [x] **Step 3: Implement** — mode check: `mode = getattr(session.kernel.ctx, "mode", "live")`; if `mode == "live"`, skip the `avg_volume` ratio branch, use `min_volume` fallback only. Keep the ratio path for backtest/replay.
- [x] **Step 4: Run tests to verify they pass** — same command → PASS.
- [x] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 719+.
```bash
git add ntrade/scanners/builtin.py tests/test_scanner.py
git commit -m "K-023 guard VolumeSpike ratio branch in live mode (unit mismatch)"
```

## Task 5 (K-024): Instrument-kind-aware trade_type default

**Files:** `ntrade/domain/orders/order.py`, `tests/test_orders.py`, `tests/test_domain_types.py`.

- [x] **Step 1: Write the failing tests** (`tests/test_orders.py`):
  - `test_equity_order_defaults_cnc` — `Equity(...).order.buy(75, price=100)` → `order.trade_type == TradeType.CNC`.
  - `test_derivative_order_defaults_mis` — `Option(...)/Future(...)` order → `TradeType.MIS`.
  - `test_explicit_trade_type_wins` — caller passes `trade_type=TradeType.MIS` on an equity → stays MIS.
- [x] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/test_orders.py -k "defaults_cnc or defaults_mis or explicit_trade_type" -q` → FAIL (MIS everywhere).
- [x] **Step 3: Implement** — helper `_default_trade_type(instrument)` → `CNC` for Equity/ETF/Spot kinds, `MIS` otherwise; thread through `buy/sell/limit/market/stop/cover/bracket` only when the caller omitted `trade_type`. Fix any existing test fixtures relying on the old default.
- [x] **Step 4: Run tests to verify they pass** — same command → PASS.
- [x] **Step 5: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 719+.
```bash
git add ntrade/domain/orders/order.py tests/test_orders.py tests/test_domain_types.py
git commit -m "K-024 derive OrderFacade trade_type default from instrument kind"
```

## Task 6 (K-025): Resample/CandleEngine label-convention parity

**Files:** `ntrade/domain/market/history.py`, `ntrade/engines/candle_engine.py`, `tests/test_history_stream.py` / `tests/test_candle_timezone.py`.

- [x] **Step 1: Write the parity test first** — build 1m candles at known UTC timestamps; resample to 5m via `HistoricalSeries.resample("5m")`; assert bar labels equal `CandleEngine._bucket`-derived end-of-bar labels (`bucket + 300`) for the same timestamps. Run it — if it passes, the conventions already align and the card becomes a documentation-only change (record the finding in the card and commit the test as a guard).
- [x] **Step 2: If misaligned, implement** — add `closed="right", label="right"` (or `origin="start_day"`/`offset` as appropriate) to the pandas `resample` call so labels match engine end-of-bar convention; keep the `dropna(subset=["open"])`.
- [x] **Step 3: Run the parity test + history tests** — `./.venv/bin/python -m pytest tests/test_history_stream.py tests/test_candle_timezone.py -q` → PASS.
- [x] **Step 4: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 719+.
```bash
git add ntrade/domain/market/history.py ntrade/engines/candle_engine.py tests/test_history_stream.py tests/test_candle_timezone.py
git commit -m "K-025 align HistoricalSeries.resample labels with CandleEngine bucketing"
```

## Task 7 (K-026): gate._equity_trace cleanup

**Files:** `ntrade/runner/gate.py`, `tests/test_paper_gate.py`.

- [x] **Step 1: Implement** — replace write-then-pop with `if e.quantity: positions[e.symbol] = (e.quantity, e.ltp)` (skip zero-quantity writes entirely); verify the docstring states balance-event yield semantics accurately.
- [x] **Step 2: Run gate tests** — `./.venv/bin/python -m pytest tests/test_paper_gate.py -q` → PASS (behavior unchanged).
- [x] **Step 3: Full suite + commit** — `./.venv/bin/python -m pytest -q` → 719+.
```bash
git add ntrade/runner/gate.py tests/test_paper_gate.py
git commit -m "K-026 simplify gate._equity_trace zero-qty handling"
```

## Definition of Done (whole batch)

- [x] Cards K-020..K-026 implemented failing-test-first (K-025 verification-first); `./.venv/bin/python -m pytest -q` → 719+ passing, no regressions.
- [x] `RateLimited` never surfaces as 0.0/empty/None from the broker boundary (executed-price + data reads); non-rate errors keep documented degrade behavior.
- [x] All 5 scanners throttle (30s); VolumeSpike ratio path is live-mode guarded; OrderFacade defaults are instrument-kind-aware; resample labels match engine buckets (or documented + guarded by a parity test); gate trace is clean.
- [x] Kanban cards K-020..K-026 moved to done (`kanban.py card move K-020 done`, ... ; `kanban.py update` per card).

## Out of scope (explicitly not in this change)

- WebSocket feed quota (only reconnect gate, already 0.5/s).
- Any falsified reviewer hypothesis (SymbolMaster race, moneyness flip, IV solver, DH-904 retry policy, `self.tsl` order bypass, gate zero-cash guard) — verified already-hardened; do not re-open.
- T-028..T-032 capability-surface gating — verified complete (`broker._gated` at dhan.py:95-104, 22 call sites routed).
- Batch-history API, product-level scanner redesigns.
