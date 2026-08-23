# nTrade — Professional-Grade Review & Roadmap

> 2026-08-21 · In-depth review of security, reliability, operations, and quality.
> Baseline: commit `f8f99b8` (post shotgun-surgery refactor), 1033 Python tests green,
> UI tsc + vitest green.

---

## Verdict

The **domain core is unusually strong** for a project of this size: idempotent order
submission with correlation IDs (`broker_executor.submit` reserves the idempotency key
*before* crossing the venue boundary), circuit breaker with proper RLock discipline,
zero-parity live/replay/backtest verified by tests, torn-line-tolerant event store,
rate-gate choke point on every REST call, and a 90% coverage floor enforced in CI config.

What separates it from a **professional application** is everything *around* that core:
no authentication on a money-adjacent API, unlocked shared state in the live pump,
per-tick file I/O on the websocket callback thread, no logging strategy, no CI, no
packaging/deployment story, and scattered env-var configuration.

---

## 1. Security

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| S1 | **API has zero authentication.** Every route — paper trade start/stop, positions, balance, kill-switch wiring, orderbook — is callable by anything that can reach the port. `routes.py`, `paper_trader.py`, `live.py` contain no auth dependency. | **High** | `grep -n "auth\|Depends\|api_key" api/routes.py api/server.py` → no hits |
| S2 | **CORS `allow_origins=["*"]` + no auth = drive-by risk.** Any web page open in a browser on the same machine can `fetch("http://127.0.0.1:8000/api/paper/start")` and control the app (localhost CSRF). Comment says "local-only desktop/dev app" but nothing enforces that. | **High** | `api/server.py:68` |
| S3 | `.env` correctly gitignored and never committed (`git log --all -- .env` empty); token JSON has `600` perms. **But** `~/.dhan/dhan-token-state.bak` is world-readable (`-rw-r--r--`) and contains the same JWT. | Medium | `ls -la ~/.dhan/` |
| S4 | WS `/ws/market` performs no origin check; combined with S2 any page can subscribe to the tick stream. | Medium | `api/live.py:ws_router` |
| S5 | No secret leakage found in logs (dhan_auth has no token/pin log statements) — good. TSL print suppression already handled in transport. | OK | — |
| S6 | Money-safety core is solid: idempotency guard reserves before broker call, releases only on deterministic rejection; missing broker order ids get `BRK-…` fallback so poll() always correlates; partial fills are delta-emitted. | OK (strength) | `broker_executor.py:94-150` |

**Fixes:** (1) bind + verify loopback-only by default and refuse `--host 0.0.0.0` without an explicit `--insecure` flag; (2) add a shared-token dependency (`X-NTRADE-Token` header generated at startup, injected into the served index.html) — ~40 LOC total; (3) restrict CORS to the Vite dev origin when `NTRADE_ENV=dev`, else same-origin only; (4) WS origin check against the served host; (5) fix `.bak` permissions in `dhan_auth.py` backup writer (`os.chmod(0o600)`).

## 2. Reliability & Concurrency

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| R1 | **LiveCandlePump shared state is unlocked.** `_clients` (set of WebSockets), `_subs` (dict), `_sec_map`, `_desired_wires` are mutated from the asyncio loop (`subscribe`/`unsubscribe`/broadcast) *and* read/mutated from the dhanhq callback thread (`ingest_tick` at `live.py:457`) and PaperTraderService's snapshot thread (`paper_trader.py:347` reads `_pump._subs`). A `subscribe` racing `ingest_tick` can raise KeyError mid-iteration or lose a subscription wire. Currently saved by GIL granularity, not design. | **High** | `api/live.py:55-56,111-163,219-239`; zero `Lock()` hits in live.py |
| R2 | **Per-tick file I/O on the callback thread.** `_record_tick` opens+appends+closes a JSONL file *for every tick* (`path.open("a")` per call), and `_persist_bar` runs a Parquet upsert — all inside the dhanhq websocket callback. A slow disk stalls the feed thread → watchdog false-trips → kill switch. | **High** | `api/live.py:243-265` |
| R3 | EventStore `append` does `flush()` but no `fsync()` — a power-loss/crash can lose the tail of the audit trail (torn lines are handled, whole lost lines are not). For an audit record backing crash recovery this matters. | Medium | `event_store.py:104` |
| R4 | 118 broad `except Exception` sites. Most are deliberate fail-safe ("a listener never breaks ingestion") and are fine; the dangerous ones are where an exception masks state corruption rather than skipping one event — e.g. `broker_executor.py:239` (poll loop) swallows without distinguishing transport-dead from transient. | Medium | sampled |
| R5 | Strengths worth keeping: EventBus RLock + MRO fan-out with error cap; breaker lock discipline in executor; feed dedup window (`_dedupe_ticks`); failure-safe PositionSyncEngine (fetch failure keeps previous state); DhanBroker raises on transport failure instead of returning fake-flat. | OK (strengths) | — |

**Fixes:** (1) one `threading.RLock` around `_subs/_clients/_sec_map/_desired_wires` mutations + snapshot reads (small, contained); (2) move `_record_tick`/`_persist_bar` onto a single background writer thread fed by a `queue.Queue` (drop-oldest, bounded) — removes disk stalls from the hot path; (3) `os.fsync(fh.fileno())` in EventStore.append behind a flag (`store=EventStore(path, durable=True)` for live, fast mode for backtests).

## 3. Production Readiness

| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| P1 | **No CI.** No `.github/workflows/`, no pre-commit, no ruff/mypy config despite ruff cache existing locally. 1033 tests exist but nothing runs them automatically. | Regressions land silently | S |
| P2 | **No logging strategy.** Only scripts call `basicConfig`. App logs go to stderr unconfigured; no levels per env, no rotation, no structured output. Answering "why did the strategy fire at 10:32?" requires reading code, not logs. | Unoperable in production | M |
| P3 | **Config scattered:** 10 `os.environ` reads across ntrade/api (`NTRADE_DATA_DIR` ×3 files, `NTRADE_TICKS_DIR` ×2, provider, event store, live-stream flag). No settings object, no startup validation ("token expired" discovered mid-session, not at boot). | Config drift, late failures | M |
| P4 | **No packaging/deployment story.** `[ui]` extra includes pywebview (desktop intent) but there's no Dockerfile, no systemd unit, no process supervision, no data-schema migration story for parquet layout changes. | Cannot deploy reproducibly | L |
| P5 | Health endpoint exists (`routes.py:190`) but is shallow — doesn't check token validity, instrument-master freshness, or feed age. `pre_deploy_check.py` exists as a script but isn't wired as a startup gate. | False-ready states | S |
| P6 | Test depth asymmetry: Python suite excellent (unit + contract + parity). UI has only unit tests (75) — no component tests, no Playwright e2e of the replay/live flows. api/ has tests but isn't under the coverage floor. | UI regressions ship | M |
| P7 | Observability: `.benchmarks/latency.json` written by a script, not runtime metrics. No counters for ticks/sec, signals, rejects, rate-gate waits exposed anywhere. | Blind operationally | M |

**Priority order:** P1 (CI: pytest + vitest + tsc + ruff on every push — half a day, protects everything else) → P2/P3 (structured logging + pydantic-settings with startup validation) → P5 (deep health) → P7 (metrics endpoint) → P4 (Dockerfile + compose; desktop packaging later) → P6 (Playwright happy-path: load chart → replay → see markers).

## 4. Code Quality Notes (post-refactor residue)

- Shims `brokers/base.py` / `capabilities.py` now emit DeprecationWarning — schedule deletion once `tests/test_brokers.py` imports are updated (the last consumer).
- `order_types.py` ROUTE_MAP is wired into `dhan.py` routing; `paper.py:126` and `broker_executor.py:122` still carry TODOs — finish the delegation when touching those files next.
- `events/registry.py` exists but nothing registers into it yet — either hook `Event.__init_subclass__` or delete it to avoid a dead abstraction.
- `docs/superpowers/plans/*.md` and `ui/.coverage` are untracked scratch — add to .gitignore or commit intentionally.
- Error-contract inconsistency remains the biggest readability debt: `ports.py` mixes `NotImplementedError` raises with silent defaults (`get_depth→None`, `get_live_pnl→0.0`). Document per-method contract or normalize.

## 5. Roadmap to "Professional"

**Phase A — Guardrails (1–2 days)**
1. GitHub Actions: `pytest -q` + `ruff check` + `cd ui && npm run typecheck && npm test` on push/PR.
2. ruff + mypy config in pyproject (start lenient, tighten incrementally).
3. Pre-commit: ruff-format + end-of-file + no-large-files.
4. Fix S3 (.bak chmod), untrack scratch files.

**Phase B — Hardening (2–4 days)**
5. API token auth + CORS lockdown + WS origin check (S1/S2/S4).
6. Pump lock + background tick-writer thread (R1/R2).
7. EventStore durable mode (R3).
8. Deep `/health`: token validity, master freshness, feed age, breaker state.

**Phase C — Operability (3–5 days)**
9. `ntrade/settings.py` (pydantic-settings): all env vars, validated at startup; fail-fast boot checks replacing ad-hoc env reads.
10. Structured JSON logging with per-module levels + rotating file handler; log every signal/fill/reject/rate-wait with correlation_id.
11. `/metrics` (prometheus-client): ticks, candles, signals, orders, gate waits, feed gaps.
12. Runbook doc: token expiry, DH-904 lockout, frozen feed, recovery procedure.

**Phase D — Ship (ongoing)**
13. Dockerfile (multi-stage: ui build → python runtime serving dist) + compose with volume mounts for data/.
14. Playwright e2e: chart loads, replay reveals markers progressively, paper start/stop roundtrip.
15. Delete deprecated shims; finish order_types delegation TODOs.

**Non-goals (deliberately deferred):** multi-user/auth-by-user, horizontal scaling, DB-backed store — the single-operator local-desktop model is coherent; harden it instead of generalizing it.
