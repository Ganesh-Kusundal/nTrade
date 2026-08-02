# Pre-Deployment System Review — findings & remediation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-08-02. Branch: `integration-completeness`.
Source: live-safety review of the production path (auth, kill-switch, position sync, crash recovery, gate scripts, rate limiting) before first live trading.

## Verdict: CONDITIONAL GO

The live path is **structurally safe on 5 of 6 review dimensions** — token hygiene, kill-switch wiring, position-sync failure safety, crash recovery, and pre-live gate scripts all pass inspection and are covered by passing tests (663). **One blocker:** broker rate limiting is not production infra yet — the current `RateLimiter(10/s)` is unsafe against Dhan's Quote 1/s quota and most TSL calls bypass it (T-025..T-032, all `planned`). **Go-live gates on the Broker Rate Limit batch.**

| # | Review item | Verdict | Evidence | Status |
|---|-------------|---------|----------|--------|
| R-1 | Token hygiene & file permissions | ✅ PASS | `os.chmod(..., 0o600)` on token + cooldown writes (dhan_auth.py:106,113,125); single SoT `DHAN_TOKEN_PATH`; 15-min expiry buffer; 90s TOTP cooldown; dead-token clear + data-plane `_login_ok` validation | D-010 done |
| R-2 | Kill-switch wiring | ✅ PASS | ACTIVATE on `RiskHaltedEvent`, DEACTIVATE on `RiskResumedEvent` (live_runner.py:186-221); all-or-nothing flag; `kill_switch_failed` critical log; feed watchdog halts on 3 missed checks (133-157) | T-024 done |
| R-3 | Position-sync failure safety | ✅ PASS | `_safe_positions`/`_safe_balance` return None on error → previous state kept, never zeroed (position_sync.py:83-93) | done |
| R-4 | Crash recovery (EventStore) | ✅ PASS | torn-line skip (T-023); unknown-type skip; `recovery_events` causal market+fills; `open_order_deltas` partial-fill rebuild (H3); one-shot `recover()` before strategies (resilient.py) | T-023/H3 done |
| R-5 | Pre-live gate scripts | ✅ PASS* | `paper_gate_run.py` exits 1 on 0 fills or >30% DD; `live_read_check.py` read-only exit 1 on FAIL; `live_smoke.py` read-only | M7 done; *T-033/T-034 below |
| R-6 | **Broker rate limiting** | ❌ **BLOCKER** | 10/s limiter only on LTP (dhan.py:68); order/status `self.tsl.*` bypass (219-333); history/chain/OHLC swallow to empty; DH-904 retried as flaky (retry.py:19-67) | **T-025..T-032 planned** |

## Review findings (new, from this pass)

| Finding | Card | Severity | Detail |
|---------|------|----------|--------|
| F-1 | B-012 | Medium | Auth `_login_ok` calls `get_ltp_data` + `get_historical_data` directly on `tsl` (dhan_auth.py:135-175) — rate-ungoverned TSL traffic, unthrottled against Quote/Data quotas. Route through the gate once T-025/T-026 land. |
| F-2 | T-033 | Low | No single pre-deploy command: operator must run `paper_gate_run.py` + `live_read_check.py` + `live_smoke.py` separately and eyeball results. Build `scripts/pre_deploy_check.py` that runs all gates and exits 1 on any failure. |
| F-3 | T-034 | Low | `live_read_check.py` reports DEGRADED rows (e.g. lot_size 0, degenerate strikes) but only FAIL rows affect the exit code (`return 1 if failed else 0`) — decide whether DEGRADED should fail closed for go-live. |
| F-4 | (covered) | — | `kill_switch` capability is a `broker.tsl.*` bypass (dhan.py:503) — already tracked in T-031 (NON_TRADING gate). |

## Verified facts (line-accurate)

- Token writes chmod 0o600: dhan_auth.py:106 (token), :113 (cooldown), :125 (clear). `_token_from_shared_store` (71-84), `_persist_shared` (94-117), `_clear_shared` (119-128), `_login_ok` (133-175).
- `LiveRunner._on_risk_halted` → `instrument.broker.kill_switch(action="ACTIVATE")` (live_runner.py:186-196); `_on_risk_resumed` all-or-nothing DEACTIVATE (198-221); `_check_feed_watchdog` halts after `_watchdog_max_missed=3` (133-157); `_on_order_timeout` cancels without halting (167-171).
- `PositionSyncEngine.sync` keeps state when `_safe_positions()` returns None (position_sync.py:29-31, 83-87); balance kept when `_safe_balance()` None (46-48, 89-93).
- `EventStore._load` skips `json.JSONDecodeError` lines (event_store.py:106-113); `recovery_events` re-sorts market+fill with append-order tiebreak (192-211); `ResilientKernel.recover()` is one-shot, rejects strategies attached, reseeds sim seq + rebuilds open-order deltas (resilient.py:36-88).
- Gate scripts: `paper_gate_run.py` returns 1 on 0 fills / >30% DD; `live_read_check.py` returns 1 if any FAIL; `live_smoke.py` returns 0.
- Rate limiter: `RateLimiter(calls_per_second=10.0)` dhan.py:68, only `_throttled()` in transport `get_ltp` (dhan_transport.py:80-97); 8 `self.tsl.*` order sites dhan.py:219-333; `RetryPolicy.execute` retries any exception (retry.py:19-67).
- Test baseline: **663 passing** (`./.venv/bin/python -m pytest -q`).

## Remediation plan

### Task A (BLOCKER): Broker Rate Limit batch — T-025..T-032
The full multi-agent plan already exists: `docs/superpowers/plans/2026-08-02-broker-rate-limit-infra.md` (3 waves, exclusive file ownership). **This is the go/no-go gate.** No additional work here beyond executing it.

- [ ] Execute `docs/superpowers/plans/2026-08-02-broker-rate-limit-infra.md` wave by wave (T-025 ∥ B-011 → T-026 ∥ B-010 → T-027 → T-028..T-032).
- [ ] Go-live only when all of T-025..T-032 are `done`.

### Task B (B-012): Gate the auth probes
**Files:** `ntrade/brokers/dhan_auth.py`, `tests/test_dhan_auth_unit.py`.
**Context:** `_login_ok` (dhan_auth.py:133-175) calls `get_ltp_data`/`get_historical_data` to validate a token. Once `BrokerRateGate` exists (T-025), the broker's `connect()` should pass its gate into the auth validation path (or auth validates via a gated transport read), so login-time probes respect Quote/Data quotas instead of bursting.

**Plumbing path (do not improvise):** `DhanBroker.connect()` → `DhanAuthProvider.authenticate()` → `get_tradehull(env, env_path)` → `_login_ok(tsl)`. The gate must flow down this chain: `_login_ok(tsl, gate=None)` and `get_tradehull(env=None, env_path=".env", gate=None)` gain an optional `gate` param. `gate=None` (the default) is a no-op — so existing callers (`check_connection.py`, tests, scripts) stay untouched and paper-safe.

- [ ] **Step 1: failing test** — mock gate + tsl; `_login_ok(tsl, gate=gate)` consumes QUOTE/DATA quota via the gate (fake clock; assert acquire called with `Quota.QUOTE` and `Quota.DATA`).
- [ ] **Step 2:** run to see it fail (`./.venv/bin/python -m pytest tests/test_dhan_auth_unit.py -q`).
- [ ] **Step 3:** route the two probe calls (`get_ltp_data`/`get_historical_data` in `_login_ok`) through `gate.acquire(Quota.QUOTE)` / `gate.acquire(Quota.DATA)` when a gate is injected; no-op when `gate is None`.
- [ ] **Step 4:** PASS + full suite `./.venv/bin/python -m pytest -q` → 663+.
- [ ] **Step 5:** `git add ntrade/brokers/dhan_auth.py tests/test_dhan_auth_unit.py && git commit -m "B-012 gate auth probe reads through BrokerRateGate"`.

### Task C (T-033): Unified pre-deploy gate script
**Files:** `scripts/pre_deploy_check.py` (new), `tests/` (smoke-level unit if feasible).
**Context:** One command an operator runs before going live. Runs, in order: (1) token freshness check (`jwt_expiry` on `DHAN_TOKEN_PATH`, warn if within buffer), (2) `paper_gate_run.py` (exit 1 on 0 fills / >30% DD), (3) `live_read_check.py` (exit 1 on any FAIL, or DEGRADED under `--strict` once T-034 lands), (4) `live_smoke.py`. Exits 1 with a combined report if any stage fails.

**Testability (mandatory):** the script must accept explicit stage paths via CLI args (e.g. `--paper-gate scripts/paper_gate_run.py --live-read scripts/live_read_check.py ...`) rather than hardcoding them, so tests can inject fake scripts and never invoke real Dhan.

- [ ] **Step 1:** failing test — point `pre_deploy_check` at a fake stage script that exits 1 → the runner propagates exit 1 (subprocess-based test, no real Dhan).
- [ ] **Step 2:** run to see it fail.
- [ ] **Step 3:** implement `pre_deploy_check.py` (subprocess orchestration with injectable stage paths, per-stage PASS/FAIL banner, aggregate exit code).
- [ ] **Step 4:** PASS + full suite.
- [ ] **Step 5:** `git add scripts/pre_deploy_check.py && git commit -m "T-033 add unified pre-deploy gate script"`.

### Task D (T-034): DEGRADED fail-closed decision
**Files:** `scripts/live_read_check.py`, `tests/` if applicable.
**Context:** Decide whether DEGRADED rows (degenerate but non-crashing reads, e.g. lot_size 0) should fail the exit code. **Recommended: yes for go-live** — a DEGRADED market-data row means a strategy may size wrong. Add `--strict` (default ON in the unified gate; OFF for diagnostics) so the exit decision is `failed or (strict and degraded)`.

**Testability (mandatory):** `live_read_check.py` connects to real Dhan via `TradingSession.connect("dhan")`, so its exit logic must be extracted into a pure function — e.g. `def exit_code(results, strict=False) -> int` at module level — so tests exercise the decision without network/credentials. `main()` calls it with the collected `RESULTS`.

- [ ] **Step 1:** failing test — `exit_code([("x", "DEGRADED", "")], strict=True) == 1` and `exit_code([("x", "DEGRADED", "")], strict=False) == 0`; FAIL rows return 1 regardless.
- [ ] **Step 2:** run to see it fail (no `exit_code` function yet).
- [ ] **Step 3:** extract `exit_code(results, strict=False)`; wire `--strict` flag through `main()`; document in the docstring.
- [ ] **Step 4:** PASS + full suite.
- [ ] **Step 5:** `git add scripts/live_read_check.py && git commit -m "T-034 fail closed on DEGRADED live reads"`.

## Definition of Done (whole review)

- [ ] R-1..R-5 findings unchanged; R-6 blocker resolved (T-025..T-032 done, suite 663+).
- [ ] B-012, T-033, T-034 implemented + committed; kanban cards `done`.
- [ ] `./.venv/bin/python -m pytest -q` green; `kanban.py update` refreshed.
- [ ] Operator runbook: `.venv/bin/python scripts/pre_deploy_check.py` before any live session.
