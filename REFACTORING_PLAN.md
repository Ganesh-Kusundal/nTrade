# nTrade — Shotgun Surgery Audit & Refactoring Plan

> Generated 2026-08-21. Audits: `ntrade/` (103 files, ~15.1k LOC), `api/` (7 files, ~2.5k LOC),
> `ui/src` (33 files, ~4.4k LOC), `scripts/` (6 files, ~836 LOC), `tests/` (107 files, ~16.4k LOC).
> **Total touched-surface across all issues: ~200 sites in ~80 files.**
> **Status 2026-08-21 21:55 IST — Executed via multi-agent team (4 waves, 11 subagents). 1033 tests passed, 4 skipped, 0 failed. UI tsc clean. ZoneInfo single-source, interval/status single-source, ghost docs all green.**

---

## 0. Executive Summary

The codebase is architecturally sound at the domain layer — Clean Architecture, event-centric
kernel, capability pattern, and zero-parity between live/replay/backtest are real strengths. The
problems are **concentration smells**: a handful of invariants (bar labeling, timestamp semantics,
symbol identity, OHLCV aggregation) are each encoded in 3–11 places, plus a sprawling
re-export/shim layer and two phantom modules documented but never shipped. The net effect is that
changes that should touch 1 file currently require synchronized edits across 4–15, with drift
already visible (stale aggregators, divergent defaults, conflicting error contracts).

The plan below is sequenced to **kill the highest change-amplification first** — every phase pays
for itself by reducing the file-count a typical future edit must touch.

---

## 1. Unified Findings Catalog

### Category A — Timestamp & Timezone Semantics (CRITICAL correctness risk)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| A1 | Naive-timestamp semantics split: IST vs UTC encoded only in comments | `quote.py:68`, `candle_engine.py:47`, `orb_vwap.py:38`, `candle_engine.py:116` | **Critical — live off-by-5.5h bug class** |
| A2 | tz-strip idiom copy-pasted 11× | `parquet_store.py` ×8, `dhan_mapper.py` ×2 | High |

**Root cause:** No shared `Timestamp` policy type. Naive datetimes are interpreted differently per
layer, discoverable only by reading comments.

### Category B — OHLCV & Bar Construction (parity-critical, 3–11 files)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| B1 | OHLCV resample aggregation implemented 3× (same agg dict, same `closed/label` rationale) | `dhan_mapper.py:119`, `history.py:124`, `candle_engine.py:89` | High |
| B2 | Intraday→daily aggregation built 3× | `marketdata.py:573`, `dhan_mapper.py:139`, `history.py:124` | Medium |
| B3 | ORB breakout definition encoded in 3 paradigms | `orb_vwap.py:56`, `orb_screener.py:70`, `screener.py:206` | Medium |

**Root cause:** No shared `BarBuilder` / `ResamplePolicy` primitive. Bar-label convention
(`closed="left", label="right"`) is the single most replicated comment in the repo.

### Category C — Symbol & Instrument Identity (4–11 files)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| C1 | Strike-label idiom `int(strike) if strike == int(strike) else strike` copy-pasted | `dhan_mapper.py:77,390`, `factories.py:47,83` | Medium |
| C2 | Option-symbol string assembled in 3 incompatible formats | `mapper L397`, `paper.py:106`, `factories L47,L83` | Medium |
| C3 | Instrument-master (SEM_*) parsed in 2 places with different grammars | `marketdata.py:212`, `dhan_transport.py:607` | High |
| C4 | Symbol↔security_id map built twice with divergent keying | `dhan_feed.py:53`, `live.py:389` | High |

### Category D — Shim / Re-export Layers (15 files)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| D1 | `BrokerAdapter` reachable via 3 paths; consumers split between them | `ports.py`, `brokers/base.py`, `brokers/__init__` + 5 prod + 8 tests | Medium |
| D2 | Capability machinery (`Capability`, `capability()`, `BrokerExtensionFacade`) shimmed, half-adopted | `ports.py`, `brokers/capabilities.py`, `dhan.py`, `base.py` | Medium |
| D3 | `events/__init__.py` aggregates only 21/29 classes; stale since creation | `events/__init__.py`, `ntrade/__init__.py` | High |

### Category E — Import Cycle (latent, high)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| E1 | Runtime cycle `base → capabilities → chain → derivatives → base` held shut only by `cached_property`/function-level imports | `base.py:117,127`, `capabilities.py:239`, `chain.py:11`, `derivatives.py:10` | **Critical — any eager-import refactor breaks the package** |

### Category F — Event Type Shotgun Surgery (4–7 files)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| F1 | Adding an event requires: define class + aggregate + `__all__` + (store registry if new module) + producer + consumer | `events/*.py`, `events/__init__.py`, `ntrade/__init__.py`, `event_store.py` | High |
| F2 | `OrderUpdatedEvent`, `OrderTimeoutEvent`, `RiskHaltedEvent`, `RiskResumedEvent`, `HeartbeatEvent`, `FeedDisconnectedEvent`, `RunnerStartedEvent`, `RunnerStoppedEvent` missing from `events/__init__.py` | `events/__init__.py` | High |

### Category G — Order Type Shotgun Surgery (5–7 files)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| G1 | Order-type dispatch via string comparison against `OrderType.value` scattered across broker + executor + simulator + backfill | `order.py:19`, `paper.py:115`, `simulator.py:80`, `broker_executor.py:134`, `dhan.py:293`, `backtest/fills.py` | High |

### Category H — Constants Drift (paper ↔ live ↔ backtest defaults)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| H1 | `risk_free=0.065` re-hardcoded 4× despite `constants.DEFAULT_RISK_FREE_RATE` | `costs.py:229`, `derivatives.py:60,200,212` | Medium |
| H2 | `initial_cash=100_000.0` duplicated in 7 signatures | `session.py:54`, `trading_session.py:55,88,108,132`, `paper.py:38`, `gate.py:50` | Medium |
| H3 | `warmup_timeout=15.0` in feed vs `WARMUP_TIMEOUT_S` constant in runner | `dhan_feed.py:222`, `constants.py:70`, `live_runner.py:33` | Medium |

### Category I — Coercion & Formatting Helpers (4× duplication)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| I1 | Scalar coercion (`_to_float`, `_to_int`, `_f`, `_first_float`, `_fill_price`) | `dhan_feed.py:39`, `dhan_mapper.py:425`, `broker_executor.py:54`, `position_sync.py:110` | Low |
| I2 | Quote dict / candle-field assembly duplicated backend ↔ frontend | `marketdata.py:472,543,717`, `market.ts`, `client.ts` | High |

### Category J — Dual Adapters & God Objects

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| J1 | Dhan websocket feed implemented twice (ntrade/sources/dhan_feed.py ↔ api/live.py) with divergent reconnect + dedup | `dhan_feed.py`, `live.py` (~120 lines duplicated) | **Critical** |
| J2 | `brokers/dhan.py` 938 LOC: transport adapter + 28 `@capability` delegations forming a second API surface | `dhan.py` | Medium |
| J3 | `broker_executor.py` 512 LOC: submission + polling + circuit-breaker + orphan adoption + crash-recovery + OMS | `broker_executor.py` | Medium |

### Category K — API/UI Contract Drift (manual double-maintenance)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| K1 | Wire types declared twice (backend dicts ↔ `ui/src/types/market.ts`); `Interval` includes `'Range'` in FE but BE rejects it | `market.ts`, `marketdata.py`, `client.ts` | High |
| K2 | `MONTHS` + `fmtExpiry()` triplicated in UI | `TradeScreen.tsx:459`, `ContractSelector.tsx:9`, `MarketHeader.tsx:17` | Medium |
| K3 | `WsStatus` union inlined 5× (1 drifted — missing `'stale'`) | `feedStatus.ts:16`, `chartStore.ts:21`, `client.ts:155`, `StatusBadge.tsx:8`, `MarketHeader.tsx:11` | Medium |
| K4 | `'NFO'` exchange default scattered across 10 sites / 5 files; `'halftrend'` string 8× / 4 files | `client.ts`, `chartStore.ts`, `TradeScreen.tsx`, `TerminalRibbon.tsx` | Medium |
| K5 | Hand-rolled request validation copy-pasted in 4 API endpoints + no response models | `routes.py` ×4, `paper_trader.py` | Medium |

### Category L — Error Handling Inconsistency

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| L1 | `BrokerAdapter` ABC: `NotImplementedError` vs `return None` vs `return 0.0` within one class | `ports.py` | Medium |
| L2 | `dhan_transport.py`: `raise BrokerDataError` vs `return None` vs bare `pass` across ~25 broad `except` sites | `dhan_transport.py` | Medium |
| L3 | Three error protocols across api (HTTPException, ws `{type:"error"}`, silent swallow) | `routes.py`, `live.py`, `marketdata.py` | Medium |

### Category M — Test & Script Fixture Explosion (largest absolute count)

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| M1 | Stub `DhanBroker` helper re-implemented (3 named copies + inline variants) | 12 files / 18 sites | **High** |
| M2 | Synthetic OHLCV builders (3 identical + 6 variants; 32 files build frames inline) | ≥9 implementations | **High** |
| M3 | Replay-kernel factory `_kernel()` per file | 12 files, 85 kernel constructions | **High** |
| M4 | `FakeClock` ×3 byte-identical, `FakeFeed` ×3, instrument/session builders ×2 | 8 files | Medium |
| M5 | FuturesMaster CSV fixture (16-col header + rows) duplicated | 4 files | High |
| M6 | Strategy stubs (`BuyOnFirstTick`, etc.) redefined per file | ~10 files | Medium |
| M7 | `datetime(2026,1,1,9,15)` ×31 across 13 files | 13 files | Medium |
| M8 | `live_smoke.py` ⊂ `live_read_check.py`; run twice by pre_deploy gate | `live_smoke.py`, `live_read_check.py`, `pre_deploy_check.py` | Medium |
| M9 | Dual backfill stacks (ntrade.data vs api.marketdata) | `backfill_parquet.py`, `backfill_futures_parquet.py` | Medium |

### Category N — Dead / Phantom Code

| ID | Issue | Sites | Severity |
|----|-------|-------|----------|
| N1 | `ntrade/replay/` is EMPTY; `ReplayEngine` documented and imported in user-guide but raises `ImportError` | `replay/`, `user-guide/06-simulation.md`, `user-guide/09-trading-flows.md` | **Critical — docs lie** |
| N2 | `ResilientKernel` referenced in ARCHITECTURE.md + graph; no implementation | `ARCHITECTURE.md:270` | Medium |
| N3 | `analytics/overlay_pipeline.py` imported by no ntrade module; coupled only via registry metadata | `overlay_pipeline.py`, `indicators.py:233` | Medium |
| N4 | `MarketHeader.tsx` (106 LOC) imported nowhere | `MarketHeader.tsx` | Low |
| N5 | Dead exports: `selectSymbol`, `strategyIndicatorKeys`, `closedBars`, `coalesceLatest`, `clipChartDays`, `inProgressBar` | `chartStore.ts:106`, `registry.ts:103`, `replayVisible.ts` | Low |
| N6 | Dead code: `factories._broker_kwargs` superseded by `_broker_kw` | `factories.py:25` | Low |

---

## 2. Phased Refactoring Plan

### Phase 1 — Safety Net: Timestamp Types & Shared Primitives
**Goal:** Eliminate the off-by-5.5h bug class and the most-replicated idioms.  
**Change amplification killed:** bar-label edits 11→1, tz edits 11→1, coercion edits 4→1.

1. **Introduce typed timestamp wrappers** in `domain/types.py`:
   - `NaiveIST(datetime)` — naive datetime that MUST be IST wall time.
   - `NaiveUTC(datetime)` — naive datetime that MUST be UTC.
   - Runtime assertion + single `to_naive_ist()` / `to_naive_utc()` converter.
   - Replace all 11 tz-strip sites + `quote.py:68`, `candle_engine.py:47`, `orb_vwap.py:38`.

2. **Extract `domain/ohlcv.py`** shared primitives:
   - `RESAMPLE_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}`
   - `BAR_LABEL_POLICY = dict(closed="left", label="right")` + docstring rationale.
   - `Bars.resample(df, rule)` — single pandas resample used by `dhan_mapper`, `history`, `candle_engine`.
   - `aggregate_daily(df)` — single intraday→daily aggregator for `marketdata.py`, `dhan_mapper`, `history`.
   - Deletes ~60 LOC across 3 files; bar-label convention edits drop from 3 files to 1.

3. **Extract `domain/coercion.py`**:
   - `to_float(v) -> float`, `to_int(v) -> int`, `first_str`, `first_float`.
   - Replace `_to_float`, `_to_int`, `_f`, `_first_float`, `_fill_price` across 4 files.

**Estimated churn:** ~250 LOC moved/deleted, 15 files touched. **Risk:** low (pure extract).

---

### Phase 2 — Break the Import Cycle & Collapse Re-exports
**Goal:** Make `domain/instruments/` statically importable; single path to every port symbol.

1. **Break cycle E1** — `chain.py:11` top-level `from .derivatives import ...` is the eager edge.
   - Move `Option`/`Future` references in `chain.py` behind a function-level import or a
     `chain._derivative(kind, ...)` factory resolved through a small `domain/instruments/_registry.py`.
   - This lets `derivatives.py` import `base.py` without completing the cycle at module load.

2. **Delete shim layer D1/D2**:
   - Delete `brokers/base.py`, `brokers/capabilities.py`.
   - Point `dhan.py`, `paper.py`, `factories.py`, `base.py`, all tests at `domain.ports` directly.
   - Update `brokers/__init__.py` to re-export from `domain.ports` (single hop, not two).
   - **Deletes ~30 LOC, removes 15-file rename surface to 1-file.**

3. **Fix stale aggregator F2/D3** — regenerate `events/__init__.py` to export all 29 classes; add
   a `make check-events` CI step that fails if any frozen dataclass in `events/` is missing from `__all__`.

**Estimated churn:** ~80 LOC, 18 files touched. **Risk:** medium (import graph surgery — run full test suite).

---

### Phase 3 — Kill Dual Adapters (Dhan Feed, Marketdata, Backfill)
**Goal:** One adapter per external system; api/ consumes ntrade/, never reimplements it.

1. **Collapse J1** — api/live.py's `LiveCandlePump` + `DhanProvider` feed wiring → delegate to
   `ntrade.sources.DhanMarketFeedSource` + `dhan_payload_to_events`. Remove api's duplicate
   reconnect state machine, `_subscriptions()` re-implementation, double-keyed `_sec_map`, and
   divergent backoff. Keep only the WebSocket multiplexing/pump layer (the part that genuinely
   belongs in api/ as a transport bridge).

2. **Collapse C3/C4** — `api/marketdata.FuturesMaster` + symbol resolution → delegate to
   `ntrade.brokers.dhan_transport.get_instrument_metadata` + `DhanMapper.to_trading_symbol`.
   api becomes a thin HTTP bridge over the ntrade domain, not a second facade.

3. **Collapse M9** — unify `backfill_parquet.py` + `backfill_futures_parquet.py` onto
   `ntrade.data.ParallelHistoryFetcher` + `ParquetStorage` + `GapDetector`. Delete the
   `api/marketdata.py` write-through path used by futures backfill.

4. **Collapse M8** — merge `live_smoke.py` into `live_read_check.py --quick`.

**Estimated churn:** ~400 LOC deleted, 8 files touched. **Risk:** medium (api contract tests needed).

---

### Phase 4 — Event & Order-Type Dispatch Tables (eliminate shotgun surgery on new types)

1. **Event registry** — central `events/registry.py`:
   - `_REGISTRY: dict[str, type[Event]]` populated by `__init_subclass__` hook on `Event`.
   - `event_store._register()` reads from it; new event = one file edit (the dataclass itself).
   - `events/__init__.py` auto-generated from registry.
   - **Adding an event drops from 4–7 files to 1.**

2. **Order-type strategy table** — `execution/order_types.py`:
   - `_STRATEGIES: dict[OrderType, OrderStrategy]` with `fill_price()`, `route()`, `validate()`.
   - `paper.py`, `simulator.py`, `broker_executor.py`, `backtest/fills.py` each call the strategy
     instead of `if/else` chains on `order_type.value`.
   - **Adding an order type drops from 5–7 files to 1.**

**Estimated churn:** ~150 LOC, 6 files touched. **Risk:** medium (preserve zero-parity contract — test fills identical).

---

### Phase 5 — Constants, Indicators & Capabilities (close the drift gaps)

1. **Constants** — enforce `domain/constants.py` as the single source:
   - Replace `0.065` literals with `DEFAULT_RISK_FREE_RATE`.
   - Replace `100_000.0` with `DEFAULT_INITIAL_CASH`.
   - Replace `15.0` with `WARMUP_TIMEOUT_S`.
   - Add a `make check-no-magic` ruff rule (or pre-commit) flagging raw `0.065`, `100_000`, `15.0`.

2. **Indicator registry honesty** — `compute_bundle` is a hardcoded if-chain despite the "zero
   call-site" claim. Replace with a `BundleSpec` table driven by the existing `registry`:
   ```python
   BUNDLE_SPECS = [BundleSpec("rsi", ["rsi_period"]), BundleSpec("atr", ["atr_period"]), ...]
   ```
   Pure fn + one entry per indicator. **Adding an indicator drops from 2–4 files to 1.**

3. **Capability table** — `dhan.py`'s 28 `@capability` delegations are pure boilerplate:
   ```python
   _DHAN_CAPS = [("depth20", _depth20), ("forever_order", _forever_order), ...]
   for _name, _fn in _DHAN_CAPS:
       capability(_name, brokers=("dhan",))(_fn)
   ```
   New capability = append one tuple.

**Estimated churn:** ~120 LOC, 5 files touched. **Risk:** low.

---

### Phase 6 — API/UI Contract Unification (shared types, single validation)

1. **OpenAPI response models** — Pydantic `BaseModel` for every response in `routes.py` +
   `paper_trader.py`. Generate `ui/src/types/market.ts` from OpenAPI via `openapi-typescript`.
   **Adds one candle/quote field: 1 file (Pydantic model), codegen regen — zero manual TS edits.**
   Eliminates K1, K5, L3.

2. **One validation model** — `CandlesRequest(symbol, interval, start, end, exchange)` Pydantic
   dependency shared by `/candles`, `/ticks`, `/chart`. Replaces 4× copy-pasted validation blocks.

3. **UI lib extraction** — `ui/src/lib/format.ts`:
   - `fmtExpiry`, `MONTHS`, `fmtPrice`, `fmtVolume` — single source (kills K2).
   - Re-export `WsStatus` from `feedStatus.ts` everywhere (kills K3).
   - `DEFAULT_EXCHANGE = Exchange.DERIVATIVES` constant (kills K4 `'NFO'` scatter).
   - Drive strategy labels + intervals from `registry.ts` (kills K4 `'halftrend'` scatter).

**Estimated churn:** ~200 LOC, 12 files touched. **Risk:** low-medium (UI tests).

---

### Phase 7 — Test & Script Infrastructure (fixture consolidation)

1. **`tests/helpers.py`** (new shared module):
   - `make_dhan_broker(**tsl_methods)` — single canonical stub (kills M1, 18 sites → 1).
   - `ohlcv(bars=20, step=1.0)` — single OHLCV builder (kills M2, 9 impls → 1).
   - `replay_kernel(timeframe="1m")` — single kernel factory (kills M3, 12 sites → 1).
   - `FakeClock`, `FakeFeed` — single copies (kills M4).
   - `futures_master_csv()` — single master-CSV writer (kills M5).
   - `STRATEGY_STUBS` dict of reusable throwaway strategies (kills M6).
   - `MARKET_OPEN = datetime(2026, 1, 1, 9, 15)` constant (kills M7).
   - `INITIAL_CASH = 100_000.0` constant.

2. **`scripts/_bootstrap.py`** (new):
   - `add_repo_root()`, `build_broker(name, env_path)` delegating to `BrokerRegistry`.

3. **Expand `conftest.py`** — wire `tests/helpers.py` fixtures into pytest.

4. **Remove phantom docs** — either ship `ntrade/replay/` + `ResilientKernel` or delete references
   in `ARCHITECTURE.md` and `user-guide/` (N1, N2).

5. **Wire `overlay_pipeline.py`** — move into `domain/analytics/` or explicit import (N3).

**Estimated churn:** ~300 LOC new helpers, ~600 LOC deleted across tests. **Risk:** low (test-only).

---

## 3. File Reorganization Map

```
ntrade/
  domain/
    types.py            # NEW: NaiveIST, NaiveUTC, Timestamp protocols
    ohlcv.py            # NEW: RESAMPLE_AGG, Bars.resample, aggregate_daily
    coercion.py         # NEW: to_float, to_int, first_str, first_float
    constants.py        # ENFORCE single source (add ruff rule)
    analytics/
      overlay_pipeline.py   # MOVE from ntrade/analytics/ (orphan fix N3)
    instruments/
      _registry.py      # NEW: break cycle E1 — lazy derivative lookup
      base.py           # EDIT: remove lazy imports, use _registry
      chain.py          # EDIT: defer derivatives import via _registry
  events/
    registry.py         # NEW: __init_subclass__ auto-registration
    __init__.py         # REGENERATE: auto-export all events
  execution/
    order_types.py      # NEW: OrderType → OrderStrategy dispatch table
    broker_executor.py  # REFACTOR: delegate to order_types strategies
  brokers/
    base.py             # DELETE (shim)
    capabilities.py     # DELETE (shim)
    dhan.py             # REFACTOR: capability table, drop to ~600 LOC
    dhan_mapper.py      # REFACTOR: use domain.ohlcv.Bars.resample
    dhan_transport.py   # REFACTOR: narrow except sites, raise-vs-return policy
  data/
    parquet_store.py    # REFACTOR: use domain.coercion, domain.types
  kernel/
    session.py          # REFACTOR: use constants.DEFAULT_INITIAL_CASH
  replay/
    __init__.py         # SHIP ReplayEngine or DELETE directory

api/
  routes.py             # REFACTOR: Pydantic request/response models
  marketdata.py         # SLIM: delegate to ntrade (drop FuturesMaster, resample, coerce)
  live.py               # SLIM: delegate to DhanMarketFeedSource
  paper_trader.py       # REFACTOR: public pump API, no private reach-ins

ui/src/
  lib/
    format.ts           # NEW: fmtExpiry, MONTHS, fmtPrice, fmtVolume
  hooks/
    useChart.ts         # REFACTOR: use api.client.request, single error contract
  api/client.ts         # REFACTOR: add post(), dedupe paperStart/Stop
  types/market.ts       # REGENERATE: from OpenAPI schema
  components/
    MarketHeader.tsx    # DELETE (dead code N4)
  # Re-export WsStatus, DEFAULT_EXCHANGE, strategy labels, intervals

tests/
  helpers.py            # NEW: shared fixtures (Phase 7)
  conftest.py           # EXPAND: wire helpers
  test_*.py             # REFACTOR: import from tests.helpers (bulk)

scripts/
  _bootstrap.py         # NEW: add_repo_root, build_broker
  live_smoke.py         # DELETE (merged into live_read_check --quick)
  backfill_futures_parquet.py  # DELETE (use backfill_parquet.py path)
```

---

## 4. Abstraction Layers (post-refactor)

```
┌──────────────────────────────────────────────────────────┐
│  api/  (thin HTTP bridge — Pydantic in, JSON out)        │
│  ui/   (thin React bridge — codegen types, client.ts)    │
├──────────────────────────────────────────────────────────┤
│  ntrade/  (domain + kernel — single source of truth)     │
│    domain/                                                │
│      types.py        NaiveIST | NaiveUTC  (timestamp)    │
│      ohlcv.py        Bars.resample | aggregate_daily      │
│      coercion.py     to_float | to_int                    │
│      constants.py    single source for ALL magic numbers │
│      instruments/    acyclic (via _registry.py)           │
│      analytics/      overlay_pipeline lives HERE          │
│      events/         registry-driven, auto-aggregated     │
│      orders/         order_type dispatch table            │
│    execution/        order_types.py (strategy per type)   │
│    brokers/          NO shims; dhan.py capability table   │
│    data/             fetcher + parquet + gap (1 stack)    │
│    sources/          ONE Dhan adapter (DhanMarketFeedSrc)  │
├──────────────────────────────────────────────────────────┤
│  tests/helpers.py    canonical fixtures (single source)   │
│  scripts/_bootstrap  shared bootstrap                     │
└──────────────────────────────────────────────────────────┘
```

**Invariant: every external system (Dhan, Parquet, UI) has exactly ONE adapter in ntrade/. The
api/ and ui/ layers are thin bridges that never re-implement framework logic.**

---

## 5. Coding Standards & Guardrails (prevent recurrence)

### Automated (add to `make check` / CI / pre-commit)

| Rule | Enforcement | Catches |
|------|-------------|---------|
| `events/__init__.py` exports all event classes | `make check-events` (AST scan vs `__all__`) | F2 — stale aggregator |
| No raw magic numbers `0.065`, `100_000`, `15.0`, `1.05` | ruff plugin or `make check-no-magic` | H1/H2/H3 — constants drift |
| `domain/` never imports `brokers/` | ruff `TCH` (type-checking) or `make check-boundary` | Layer violation |
| Single `BrokerAdapter` import path | ban `from ntrade.brokers.base import` (fix D1) | D1 — shim drift |
| No top-level `from .derivatives import` in `chain.py` | `make check-no-cycle` (import-graph test) | E1 — cycle resurrection |
| `conftest.py` provides canonical fixtures | lint: warn on `DhanBroker.__new__` outside helpers | M1 — fixture duplication |
| Pydantic response models for all routes | mypy strict on routes.py | K5, L3 |

### Convention (documented in `AGENTS.md` or `CONTRIBUTING.md`)

1. **Timestamps:** always `NaiveIST` or `NaiveUTC`; never bare `datetime` in public APIs.
2. **Bar construction:** always `Bars.resample` + `RESAMPLE_AGG`; never hand-roll OHLCV agg.
3. **New event:** define the frozen dataclass only — registry handles the rest.
4. **New order type:** one entry in `order_types.py` dispatch table; no `if/else` in call sites.
5. **New capability:** append one tuple to `_DHAN_CAPS`; never a new `@capability` boilerplate block.
6. **Test fixtures:** import from `tests.helpers`; never define your own `DhanBroker.__new__` / `_ohlcv` / `_kernel`.
7. **API contract:** backend owns the Pydantic model; frontend regenerates TS types — never hand-maintain `market.ts`.
8. **Constants:** `domain/constants.py` is the only source; magic numbers are a review blocker.

---

## 6. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking zero-parity (live ↔ replay ↔ backtest) | Phase 4 (order types) gated by `test_zero_parity` passing before + after |
| Breaking import graph during cycle break (Phase 2) | Run full test suite + `python -c "import ntrade"` smoke before any shim deletion |
| API contract break (Phase 3) | api/ test suite must pass; add contract tests for `/candles` `/ticks` `/chart` response shapes |
| UI type drift (Phase 6) | `openapi-typescript` regen + `tsc --noEmit` gate in CI |
| Test fixture consolidation changing test semantics | Keep helper signatures identical to existing stubs; run suite per-file after refactor |
| `overlay_pipeline.py` move (N3) | Verify `indicators.py:233` registry spec still resolves after move |

---

## 7. Phase Sequencing & Dependencies

```
Phase 1 (types + primitives)  ─┐
Phase 2 (cycle + shims)       ─┤── independent, can parallelize
Phase 3 (dual adapters)       ─┘
        │
Phase 4 (dispatch tables)     ← needs Phase 2 done (clean import graph)
        │
Phase 5 (constants + registry)← independent, can parallel with Phase 4
        │
Phase 6 (API/UI contract)    ← needs Phase 3 done (api/ slimmed)
        │
Phase 7 (test/script infra)  ← last; depends on stable domain API
```

**Recommended order:** 1 → 2 → 3 → 4 → 5 → 6 → 7, with 1+2 parallelizable.
**Estimated total churn:** ~500 LOC added, ~1.2k LOC deleted, ~60 files touched.
**Estimated total elimination:** ~200 duplicate sites → ~20 (canonical definitions).
