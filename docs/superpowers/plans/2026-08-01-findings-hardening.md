# G3 — Findings Hardening: Zero-Parity, Thread-Safety & Money-Path Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every finding from the end-to-end multi-agent review (2026-08-01): broker wall-clock timestamps (zero-parity), the D-007 lock bypass, silent-zero LTP reads, recovery-state rebuild, EventStore causal ordering, an Indian-market cost model, a stale-feed halt, OMS/order-id edge cases, registry/test landmines, and low-severity footguns.

**Baseline:** 564 passing tests. Test runner: `./.venv/bin/python -m pytest -q` (run from `/Users/apple/Downloads/nTrade`).

## Global Constraints

- Test command is always `./.venv/bin/python -m pytest -q`.
- Events stay `@dataclass(frozen=True, kw_only=True)` extending `ntrade.events.base.Event`; `ts` from the kernel clock, never `datetime.now()`.
- Broker timestamps must come from an injected clock/reference — no `now or datetime.now()` fallbacks.
- Domain layer stays broker-agnostic.
- Each task's gate: failing test first, then implementation, then full suite green.
- Kanban cards K-001..K-019 track each finding.

---

## Task Group A — Zero-parity (P0)

### Task A1 (K-001/C1): Broker clock injection — remove `now or datetime.now()` fallbacks

**Files:** `ntrade/brokers/base.py`, `ntrade/brokers/dhan.py`, `ntrade/brokers/paper.py`, `ntrade/brokers/dhan_mapper.py`, `ntrade/brokers/dhan_transport.py`

- [ ] `get_quote`/`get_depth`/`get_orderbook`/`get_trade_book`/`get_historical` accept a mandatory `now`/`asof` (or a broker clock) — no silent wall-clock fallback
- [ ] `paper.py` gets an injectable clock (defaults to a real clock for legacy callers, but replay/backtest passes the kernel clock)
- [ ] `dhan.py:820` history cutoff uses `asof` not `datetime.now()`
- [ ] `dhan.py:620` expiry default uses `asof.date()` not `date.today()`
- [ ] Test: replay determinism — two backtests run on different wall-clock days produce identical history windows/fills

### Task A2 (K-002/C2): Reroute `ctx.instruments` direct iterators through lock-guarded accessors

**Files:** `ntrade/runner/live_runner.py`, `ntrade/scanners/builtin.py`, `ntrade/kernel/resilient.py`, `ntrade/kernel/trading_session.py`, `tests/test_context.py`

- [ ] `live_runner.py:147,156,177` → `ctx.instruments_snapshot()`
- [ ] `scanners/builtin.py:22` → `ctx.instruments_snapshot()`
- [ ] `resilient.py:125` → snapshot (already per-item `.snapshot()`)
- [ ] `trading_session.py:274` → snapshot
- [ ] Strengthen `test_context.py` with a deterministic `threading.Barrier` storm that fails without the lock + `assert not t.is_alive()` after join

### Task A3 (K-003/H1): `get_ltp`/`get_quote` raise on total failure

**Files:** `ntrade/brokers/dhan_transport.py`, `tests/test_dhan_transport_raises.py` (new)

- [ ] `get_ltp` raises (e.g. `BrokerDataError`) after retries exhausted instead of returning `0.0`
- [ ] `get_quote` propagates; callers that previously swallowed now surface
- [ ] Test: dead broker → `pytest.raises` on `get_ltp`/`get_quote`; no zero-price quote reaches risk

---

## Task Group B — Recovery & Replay (P1)

### Task B1 (K-004/H3): Partial-fill delta state rebuildable on recovery

**Files:** `ntrade/execution/broker_executor.py`, `ntrade/kernel/resilient.py`

- [ ] `BrokerExecution` derives last-seen filled qty from broker-reported cumulative qty (idempotent across restart)
- [ ] `ResilientKernel` recovery test asserts partial fills don't double-count after recover()

### Task B2 (K-005/H5): EventStore monotonic sequence for causal ordering

**Files:** `ntrade/storage/event_store.py`, `ntrade/kernel/resilient.py`, `ntrade/kernel/event_bus.py`

- [ ] Events recorded with a monotonically increasing `seq`
- [ ] `recovery_events()` orders by `seq` (not ts) so fills replay after the causing market event
- [ ] Test: two events sharing a ts preserve causal order

---

## Task Group C — Money Path (P1)

### Task C1 (K-006/H6): Indian-market cost model

**Files:** `ntrade/execution/costs.py`, `tests/test_costs_india.py` (new)

- [ ] STT (0.1% equity sell / 0.125% F&O sell), exchange txn charges, SEBI fee, GST on brokerage, stamp duty
- [ ] `BacktestSimulator`/`SimulatedExecution` wire the new cost components
- [ ] Test: known trade → exact expected total cost

### Task C2 (K-013/M7): Pre-live gate scripts fail closed

**Files:** `scripts/paper_gate_run.py`, `scripts/live_read_check.py`

- [ ] Non-zero exit when fills==0 / drawdown over limit / read-check timeout
- [ ] Smoke both scripts

### Task C3 (K-007/M1): Stale-feed guard — halt on frozen feed

**Files:** `ntrade/runner/live_runner.py`, `ntrade/engines/risk_engine.py`

- [ ] No tick for N×poll_interval → `RiskHaltedEvent` (clock-injected deadline)
- [ ] Test: silent feed trips the halt and rejects subsequent signals

---

## Task Group D — OMS & Registry (P2)

### Task D1 (K-008/M2): BRK- fallback order id merging
- [ ] When the real broker id arrives, merge into the tracked record (no duplicate OMS entries)

### Task D2 (K-009/M3): StrategyRunner refcount leak guard
- [ ] Context-manager `with runner:` semantics or finalizer; `remove()` decrements on all exit paths

### Task D3 (K-010/M4): `unregister_all()` resets `_DEFAULT_BROKERS_REGISTERED`
- [ ] Test: unregister → get() re-registers defaults

### Task D4 (K-011/M5): `instruments_snapshot()` deep-snapshot semantics documented/locked
- [ ] Document shallow semantics; provide `instruments_deep_snapshot()` under lock if needed

### Task D5 (K-012/M6): Scanner rate-limit + snapshot semantics
- [ ] Paced `get_quote` per instrument; scan against a consistent snapshot

### Task D6 (K-014/M8): pandas leak at transport boundary
- [ ] `get_positions`/`get_holdings`/`get_long_term_historical`/`get_daily_historical` return domain types (or documented records) instead of raw `pd.DataFrame`

---

## Task Group E — Low-severity footguns (P2/P3)

### Task E1 (K-015/L1): indicators bare-pass fallbacks → log + NaN
- [ ] `indicators.py:162-197` no silent no-ops

### Task E2 (K-017/L3): deterministic concurrency test (folded into A2)
- [ ] Barrier storm in `test_context.py`

### Task E3 (K-018/L4): SymbolMaster cache synchronization
- [ ] `SymbolMaster.get`/`invalidate`/`clear` take a lock

### Task E4 (K-019/L5): Greeks zero-filled footgun
- [ ] Distinguish "not computed" from 0.0 (sentinel or raise-on-access)

### Task E5 (K-016/L2): Market vs TradingSession consolidation — backlog (P3)
- [ ] Document `Market` as thin legacy shim over `TradingSession`; no behavior change this pass

---

## Final Gates

- [ ] Full suite green: `./.venv/bin/python -m pytest -q`
- [ ] Kanban: move K-001..K-019 to `done` (or note deferred)
- [ ] Code review of all diffs
- [ ] `.superpowers/sdd/progress.md` updated
