# ntrade — Architecture

An institutional-grade, object-oriented trading framework where **every financial
entity is a rich domain object**. Users interact with market objects, never with
REST endpoints, websocket payloads or JSON.

```python
nifty = m.index("NIFTY")
nifty.quote           # Quote object
nifty.ltp             # float
nifty.history("5m")   # HistoricalSeries (dataframe-like, attached)
chain = nifty.option_chain()
chain.atm.greeks.delta
atm = chain.atm
atm.order.buy(75)
```

---

## 1. Layered architecture (Clean Architecture)

```
┌─────────────────────────────────────────────────────────────┐
│  PUBLIC API (facade + factories)                            │
│  TradingSession · Market · InstrumentFactory · SymbolMaster│
│  BrokerRegistry                                            │
├─────────────────────────────────────────────────────────────┤
│  TRADING KERNEL  (event-centric — zero parity)              │
│  TradingKernel · ResilientKernel · StrategyRunner · LiveRunner│
│  EventBus · TradingClock (Live/Replay/Sim) · EventStore   │
│  engines/  market · candle · indicator · strategy · risk   │
│           order (OMS) · portfolio · position-sync          │
│  execution/  router → SimulatedExecution · BrokerExecution │
│  replay/  ReplayEngine                                       │
│  backtest/ BacktestSimulator · fills (FillPolicy)           │
│  sources/ MarketFeedSource · DhanMarketFeedSource · Synthetic│
│  sim/ synthesize_1m_ticks · SimTick                         │
│  data/ ParallelHistoryFetcher · ParquetStorage · GapDetector│
│      · ScannerLoader · universe                             │
├─────────────────────────────────────────────────────────────┤
│  DOMAIN LAYER  (pure Python, no broker imports)             │
│  instruments/  market/  analytics/  orders/  session/       │
│      Instrument (state + behaviour)                         │
│      Equity · Index · ETF · Currency · Commodity · Bond     │
│      Crypto · Spot · Future · Option · SyntheticInstrument  │
│      OptionChain (Composite)                                 │
│      Quote · Tick · MarketDepth · HistoricalSeries · LiveStream│
│      CandleSeries · Greeks · BlackScholes · indicators      │
│      Order · OrderFacade · OrderBook · TradeBook            │
├─────────────────────────────────────────────────────────────┤
│  BROKER LAYER  (adapters — hidden behind domain objects)    │
│  BrokerAdapter ABC → PaperBroker · DhanBroker (+ auth)      │
│  DhanTransport · DhanAuthProvider · DhanMapper               │
│  Capability pattern → instrument.broker.<cap>()             │
├─────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE (transport, persistence, rate-gate)        │
│  BrokerRateGate · RetryPolicy · token store, cooldown file │
└─────────────────────────────────────────────────────────────┘
```

Dependency rule: the domain layer imports nothing from the broker layer.
Instruments receive a `BrokerAdapter` via **constructor injection** (dependency
injection), and access broker-specific capabilities through a capability facade
that only resolves at call time. `BrokerAdapter` itself lives in
`domain/ports.py` (the `brokers/base.py` and `brokers/capabilities.py` files are
re-export shims over it).

### Graph observation — the Equity wiring hub
`Equity` is the knowledge graph's most-connected node (307 edges across 30+
communities per the graphify graph built 2026-08-04). That density is
**test-fixture reuse, not hidden coupling**: Equity is the default instrument
across the test suites and every engine/execution seam, so the graph's edges
are construction sites (`tests/test_*.py` → `Equity`), not dependency arrows.
The same pattern holds for `PaperBroker` (172 edges), `TradingKernel` (169
edges) and `ReplayClock` (164 edges): heavily-wired composition roots and
fixtures. Read community density as "how often this is constructed/used in
tests", not as architectural coupling — the dependency rule above is the
authoritative coupling statement, and it is enforced by the import boundary
(`domain` never imports `brokers`; `brokers/base.py` and `brokers/capabilities.py`
are re-export shims over `domain/ports.py`). The graph also flags a 4–5-file
import cycle in `domain/instruments/` (`base → capabilities → chain → {expiry,
derivatives} → base`); this is a deferred hygiene issue surfaced by graphify,
not an active runtime problem because the cycle members are `cached_property`
lazily resolved at call time, not module-load imports.

## 2. Domain object hierarchy

```
Instrument (ABC)                     — state + behaviour for every market entity
├── Equity      (NSE default)        — market_cap, delivery intraday cost uplift
├── Index       (INDEX default)      — option_chain() entry point
├── ETF         (NSE)
├── Currency    (BSE)
├── Commodity   (MCX)
├── Bond        (BSE)
├── Crypto      (CRYPTO)
├── Spot        (NSE)
├── Future      (NFO)                — basis, cost_of_carry, rollover, continuous
├── Option      (NFO)                — strike, expiry, greeks, BS, IV, payoff
└── SyntheticInstrument              — composition of legs (straddles etc.)

OptionChain (Composite)              — contains many Option instruments
  .calls .puts .expiries .atm .itm .otm .nearest_expiry
  .max_pain() .pcr() .iv_surface() .greeks() .subscribe()

Scanner / ScannerFacade / ScannerResult  — market scanner abstractions
Strategy (base) / StrategyEngine     — event-reactive strategy hooks
Event (base)                          — canonical frozen dataclass events
```

### State owned by every Instrument (no global state)

| State             | Type              | Notes                              |
|-------------------|-------------------|------------------------------------|
| `_quote`          | `Quote` (frozen)  | ltp, bid, ask, OHLC, volume, oi    |
| `_depth`          | `MarketDepth`     | order book snapshot                |
| `_history`        | `HistoricalSeries`| df + cache + timeframe + freshness |
| `_stream`         | `LiveStream`      | subscription state, ticks, handlers|
| `_indicators`     | `dict`            | computed indicator bundle (`ema_<n>`, `sma_<n>`, `rsi_<n>`, `atr_<n>`, `vwap`, `stx_<n>`) |
| `_signals`        | `dict`            | strategy/pattern signals (`set_signal`/`get_signal`) |
| `_corporate_actions` | `list[CorporateAction]` | dividends, splits, bonuses |
| `_tags/_annotations/_metadata` | dicts/set | user extensibility            |
| `_session`        | `TradingSession`  | market state (open/closed/... )    |
| `broker_adapter`  | `BrokerAdapter`   | injected transport (`broker` kwarg in ctor)|
| `tick_size/lot_size/freeze_qty` | optional | hydrated from broker via `hydrate()` |

### Capability objects (accessed via Instrument methods, not direct attributes)

| Capability         | Method           | What it does                          |
|--------------------|------------------|---------------------------------------|
| `MarketCapability` | `.market`        | `.refresh() .quote .depth .ltp .ohlc`  |
| `StreamCapability` | `.stream()`      | `.subscribe() .unsubscribe() .on_tick()`|
| `AnalyticsCapability` | `.analytics` | `.compute_indicators() .rsi() .atr()` |
| `DerivativesCapability` | `.derivatives` | `.option_chain()` (Index)            |
| `BrokerExtensionFacade` | `.broker`    | dynamic `@capability` lookup          |

## 3. Behaviours — "Tell, Don't Ask"

Every method the mission specifies lives on the owning object:

- **Quote access** — `.quote .ltp .bid .ask .volume .oi .vwap .spread() .mid_price() .is_stale()`
- **History** — `.history("5m")` (call form), `.history.fetch() .refresh() .download() .cached() .live_merge() .resample() .indicators()`
- **Live data** — `.subscribe() .unsubscribe() .is_live .last_tick .ticks() .candles() .on_tick() .on_quote() .on_trade() .on_depth() .on_disconnect() .on_reconnect()`
- **Orders** — `.order.buy() .sell() .limit() .market() .stop() .cover() .bracket() .place()`
- **Analytics** — `.indicators .compute_indicators() .rsi() .atr() .vwap() .supertrend() .detect_absorption() .detect_imbalance() .detect_breakout() .statistics()`
- **Corporate actions** — `.record_corporate_action() .corporate_actions .clear_corporate_actions()`
- **Metadata** — `.hydrate() .download() .set_signal() .get_signal() .signals`
- **Identity** — `.clone() .serialize() .snapshot() .session() .tag() .annotate()`

## 4. Broker extension model (Capability pattern)

Base instruments stay **broker-agnostic**. Broker-specific capabilities are
registered in a global capability registry and resolved dynamically:

```python
@capability("depth20", brokers=("dhan",))
def _depth20(instrument, levels=20): ...

nifty.broker.depth20()     # works — Dhan supports it
rel.broker.depth30()       # AttributeError — no broker supports it
```

Capabilities declare their supported brokers (`None` = all). The
`BrokerExtensionFacade` on `instrument.broker` looks up the capability by name,
checks the current broker supports it, and fails fast otherwise — no giant
`if/else`, open/closed against new brokers.

Broker adapters also decompose into transport, auth and mapping layers:
`DhanBroker` composes `DhanTransport` (all REST calls through
`_invoke(Quota, fn)` → `BrokerRateGate`), `DhanAuthProvider` (token store +
cooldown + PIN/TOTP fallback), and `DhanMapper` (wire→domain normalization).
Dhan's chain-frame parsing lives in `dhan_mapper.py` `DhanMapper` /
`chain_from_dhan_df` → `OptionChain`, called from `DhanBroker.get_option_chain`,
so the domain layer stays broker-agnostic. Bracket (BO) orders route through the
adapter to Dhan's `place_super_order` API; `get_instrument_metadata()` hydrates
tick/lot/freeze qty into instruments via `Instrument.hydrate()`.

## 5. Historical & live data lifecycle

```
history.fetch(timeframe, days, start, end, force)
  └─ cached? fresh? ──> return cache
  └─ broker.get_historical() ──> DhanTransport._invoke(Quota.DATA) ──>
     BrokerRateGate (5/s) ──> normalize OHLCV ──> store + mark fresh

stream.subscribe()
  └─ broker.subscribe(instrument)   # transport multiplexes under the hood
  └─ stream state -> SUBSCRIBED
broker pushes ticks ──> stream.ingest_tick(tick)
  └─ updates _quote (immutable replace) ──> emits tick/quote/trade/depth events
history.live_merge()  # converts raw ticks into OHLCV candle rows, schema-safe
```

### Backfill / scanner data layer (`ntrade/data/`)
`ParallelHistoryFetcher` fans multi-instrument historical fetches out across a
`ThreadPoolExecutor` (4 workers, derived from the DATA quota of 5/s). Each worker
calls `broker.get_historical()` which routes through `DhanTransport._invoke(Quota.DATA,
fn)` — the shared `BrokerRateGate` serializes all calls to 5/s, so workers never
exceed the quota. Results are normalized into a tagged DataFrame
(`symbol, exchange, kind, timeframe` + `strike/option_type/expiry` for
derivatives) and stored via `ParquetStorage.upsert()` (idempotent: deletes
overlapping rows then appends; tz-naive IST timestamps; Hive partitioning
`symbol=.../year=.../month=...`). `GapDetector.detect()` diffs a requested
universe against stored data to find missing ranges; `fetch_missing` refills only
the gaps. `ScannerLoader.load_universe()` wraps `ParquetStorage.read()` for
partition-pruned 2–3-month reads across 100s of symbols. `load_universe()` maps
Nifty constituent CSVs to `Equity` instruments via `InstrumentFactory`. Requires
`pyarrow` + `duckdb` (both in `pyproject.toml`); `ParquetStorage.duckdb_scan()`
registers a Hive-partitioned DuckDB view for SQL queries.

## 6. Event model & Observer

`LiveStream` is a per-instrument observer target with named channels
(`tick`, `quote`, `trade`, `depth`, `disconnect`, `reconnect`). Handlers
are registered via `on_*` (decorator-friendly) and errors are swallowed so a
handler can never kill the stream. The broker adapter multiplexes one transport
across all subscribed instruments (`_subscriptions` dict on `BrokerAdapter`).

## 6b. Event-centric trading kernel (zero parity)

The framework is **event-centric**, not broker-centric: brokers/replays/simulators
are interchangeable *event sources*, and instruments become *read models*
updated by events rather than pulling state via `refresh()`.

```
Exchange ──► Dhan ──► TickEvent ──► EventBus
DuckDB   ──► Replay ──► TickEvent ──► EventBus
OHLCV    ──► BacktestSimulator ──► TickEvent ──► EventBus
        ──► MarketEngine ──► Instrument (read model) ──► QuoteUpdated
        ──► CandleEngine ──► CandleClosed ──► IndicatorEngine ──► IndicatorUpdated
        ──► Strategy ──► SignalGenerated ──► RiskEngine ──► Approved/Rejected
        ──► OrderEngine (OMS) ──► OrderIntent ──► ExecutionRouter
             ──► SimulatedExecution | BrokerExecution ──► Accepted/Filled
             ──► PortfolioEngine ──► PositionUpdated + BalanceChanged
```

- **Canonical events** — frozen dataclasses in `events/` (market, order,
  portfolio, risk, lifecycle); `ts` always comes from the `TradingClock`.
- **EventBus** — tiny synchronous pub/sub; base-class subscription via MRO;
  handler errors are swallowed so one bad subscriber never kills the kernel.
- **TradingClock** — `LiveClock` (wall), `ReplayClock`/`SimulationClock`
  (deterministic). Strategies use `ctx.now()`, never `datetime.now()`.
- **TradingKernel** — wires all engines; `mode` is live | replay | backtest.
  The engine stack is identical in every mode; only the event source, the
  execution target (router) and the clock are interchangeable.
- **Zero parity** — the same event stream through `TradingKernel(mode=...)`
  produces the same fills in live, replay and backtest (verified by test).
- **Execution targets** — `SimulatedExecution` (paper/backtest, with
  slippage/commission cost models) and `BrokerExecution` (routes intents to a
  `BrokerAdapter`, keeping transport hidden).
- **EventStore** — append-only JSONL event record for deterministic replay,
  auditing and crash recovery; nested events round-trip.
- **Instrument projection** — `Instrument.apply_quote()` / `apply_depth()` /
  `set_indicators()` are the read-model seams the kernel writes into.
- **Event sources** — `MarketFeedSource` (ABC) turns any market data producer
  into canonical events. `SimulatedFeedSource` feeds a deterministic price
  path or OHLCV frame (offline stand-in for a broker websocket).
- **Kernel recording** — pass `store=EventStore(...)` to `TradingKernel` to
  append every published event (market, signals, fills, lifecycle) for audit
  and crash recovery. Replay only `store.market_events()` — the causal
  market stream — so the kernel recomputes derived decisions deterministically
  (record → replay produces identical fills).
- **Backtest hardening** — `BacktestResult` reports `commissions_total` and
  `max_drawdown_pct`; `BacktestSimulator(fill_policy=FillPolicy())` swaps in a
  `BarAwareExecution` so LIMIT orders only fill when a bar trades through the
  limit (MARKET orders keep the standard path).
- **ResilientKernel** — crash recovery on top of the recorded EventStore:
  `store.recovery_events()` returns the causal market stream
  (`Tick/Quote/Depth` + `OrderFilled`) in causal order (a fill replays *after*
  the market event that caused it); `ResilientKernel.recover()` replays it into
  a fresh kernel with **no strategies attached** (fills are re-applied via the
  execution/portfolio engines, never re-created by re-running strategies),
  pauses event recording during replay so recovery isn't re-appended to the
  audit store, then exposes `snapshot()` / `last_event_ts()` (the resume point
  over the causal stream only). Recovery is one-shot (a second `recover()`
  raises) and reseeds the simulator sequence so the first live order never
  collides with recovered `SIM-…` ids. Recovered fills, positions and balance
  match the crashed session (verified by zero-parity test).
- **StrategyRunner** — multi-strategy management on one kernel. Each strategy
  registered via `runner.add(strategy, risk={...})` gets a unique name
  (`strategy`, `strategy#2`, …) — also avoiding collisions with strategies
  registered directly on the kernel — and its **own RiskEngine scoped to that
  name** (per-strategy quantity/notional/positions/allowlist limits stay
  isolated; `max_positions` counts only positions opened by that strategy).
  The kernel's global RiskEngine is paused while the runner owns strategies so
  a signal is never screened twice, with a shared reference count so multiple
  runners can manage simultaneously and the global engine is restored exactly
  once when the last manager releases. `remove()` hot-detaches, `enable()`/
  `disable()` toggle, `status()` reports limits + approve/reject counts,
  `release()` restores the global RiskEngine.
- **Reusable strategies** (`ntrade/engines/strategies.py`) — ready-made
  `Strategy` subclasses on the canonical hooks, interchangeable across
  live/replay/backtest. `EmaCrossStrategy(fast=9, slow=21)` is an always-in-
  market EMA crossover that reverses instead of stacking (BUY on golden cross
  when flat/short, SELL on death cross when flat/long) and reads its EMA values
  `sma_periods=()`), and reads its EMA values from the IndicatorEngine's bundle
  (or computes them itself via the pure `ema`/`sma` functions).
- **DhanMarketFeedSource** — the live Dhan websocket (`dhanhq.MarketFeed`, a
  Dhan-Tradehull dependency) wrapped as a `MarketFeedSource`. `feed_factory` is
  injectable for offline tests; the default builds a real feed lazily (credentials
  only needed at runtime, not import time). The pure `dhan_payload_to_events`
  mapper translates Dhan v2 payloads (`Ticker/Quote/Full/Market Depth`) into
  canonical events; non-dict payloads (status/disconnect packets), unknown
  securities, malformed prices and malformed numeric fields are all skipped so
  a bad wire message never breaks the kernel. Start/stop are non-blocking (feed
  runs in a background thread) and restartable (a closed feed is discarded and
  rebuilt on the next start); `mode='depth'` is rejected under the v2 API,
  which forbids the depth subscription code.
- **Live kernel execution** — DhanBroker wired into the kernel as the live
  execution target: `TradingKernel(broker=...)` routes approved signals through
  `BrokerExecution` → `instrument.order.place` → Dhan OMS. Orders are
  asynchronous: `submit()` publishes `OrderAcceptedEvent` immediately and tracks
  the open order; `kernel.poll_orders()` refreshes broker status and publishes
  `OrderFilledEvent` / `OrderRejectedEvent` as the lifecycle advances. Fills are
  partial-safe and idempotent: `poll()` emits only the newly-filled delta
  (`PARTIAL → COMPLETE` emits 3 then 2, never 5 twice), and a partially-filled
  order that is later rejected/cancelled keeps its filled shares as a fill
  (rejecting only the remainder). Missing broker order ids get a local
  `BRK-…` fallback so poll() can always correlate. `PositionSyncEngine`
  (`kernel.sync_positions()`) reconciles broker-reported positions/balance into
  the kernel's read models, publishing the same canonical `PositionUpdatedEvent`
  / `BalanceChangedEvent` as an internal fill, and is failure-safe: a failed
  fetch keeps the previous state (it never wipes positions or zeroes the
  balance). To make "flat" distinguishable from "error", `DhanBroker.get_positions`
  / `get_balance` now raise on transport failure instead of collapsing to `[]` / `0.0`.
  Live, replay and backtest observe identical events (zero parity, verified by
  test with a stubbed Tradehull).
- **LiveRunner orchestration harness** — `LiveRunner(kernel, feed)` closes the
  loop a strategy needs to run unattended: it starts the kernel and feed,
  `step()`s every `poll_interval` seconds calling `kernel.poll_orders()` /
  `kernel.sync_positions()`, and subscribes to `RiskHaltedEvent` →
  `RiskResumedEvent` so a tripped circuit breaker immediately fires
  `instrument.broker.kill_switch(action="ACTIVATE")` on every broker-backed
  instrument (and `DEACTIVATE` on resume). It also subscribes to
  `FeedDisconnectedEvent` (→ `_halt_risk`, fail-closed), `HeartbeatEvent`,
  `OrderFilledEvent` (logging), and `OrderTimeoutEvent` (only cancels the stale
  order — does NOT trip the kill switch, by contract — see
  `tests/test_contract_live_consumers.py`). The feed watchdog monitors tick
  velocity wall-clock style and, on a frozen feed (no ticks for
  `watchdog_timeout`), routes through `RiskEngine.halt()` (fail-closed).
  `_cancel_resting_orders()` cancels every tracked open order on stop (opt-out
  via `cancel_on_stop=False`, logged). Time is injectable (`_timer`/`_sleep`)
  so the loop is unit-testable without wall-clock waits. Lifecycle is published
  as `RunnerStartedEvent` / `RunnerStoppedEvent`.
- **Synthetic feed (synth mode)** — `SyntheticMarketFeedSource` extrapolates a
  1-minute OHLCV frame (e.g. `DhanBroker.get_historical`) into one `TickEvent`
  per simulated second via the pure, seeded `synthesize_1m_ticks`: prices stay
  within high/low, the first tick is open and the last is close, both extremes
  are touched exactly, and per-second volume sums to the bar volume. A
  `CandleEngine` fed only these ticks reconstructs the source bars exactly
  (test-enforced). `build_source(kernel, feed="synth"|"live")` is the single
  synth/live flag; `scripts/live_runner_run.py` rehearses the full pipeline
  offline before any real websocket.
- **Risk circuit breakers** — `RiskEngine` gained `halt()`/`resume()`/`equity()`
  and three breakers: `max_daily_loss` (equity vs session start), `max_drawdown_pct`
  (peak-to-trough), and `price_deviation_pct` (fat-finger guard against ltp).
  Once halted, every signal is rejected until `resume()`. Breaking publishes
  `RiskHaltedEvent` (consumed by the LiveRunner's kill switch) and `resume()`
  publishes `RiskResumedEvent`. All new kwargs are optional, so `StrategyRunner`
  and existing kernels are untouched.
- **OMS state events + modify/cancel + orphan adoption + crash-restore** — `BrokerExecution.poll()` publishes `OrderUpdatedEvent` whenever an open order's status changes between polls (PENDING → PARTIALLY_FILLED → COMPLETED / CANCELLED / REJECTED), alongside the partial-safe fills. `BrokerExecution.modify(order_id, **kw)` / `.cancel(order_id)` delegate to the adapter. `TradingKernel` exposes `open_orders()`, `modify_order()`, `cancel_order()` passthroughs (no-ops in sim mode). `BrokerExecution.reconcile_open()` adopts broker-side orphan orders unknown to the tracker (C-4) so an ambiguous placement failure loses nothing; `BrokerExecution.restore_open(deltas)` rehydrates the in-memory `_open` map from `EventStore.open_order_deltas()` during `ResilientKernel.recover()` (H3) so a partially-filled order's remaining quantity survives a crash.
- **Ops tooling** — `scripts/paper_gate_run.py` replays real historical data
  through the synthetic feed with a strategy and prints a `build_paper_report`
  checklist (fills, final equity, max drawdown) that must pass before going
  live; `scripts/benchmark_latency.py` measures kernel tick throughput and
  writes `.benchmarks/latency.json`.

### Infrastructure — rate limiting, retry, resilience

- **BrokerRateGate** — the single choke point every outbound broker REST call
  passes through. Multi-window, multi-class sliding-window gate (real deque
  timestamps, not a spacer): `Quota.QUOTE` (1/s), `Quota.DATA` (5/s, 100k/day),
  `Quota.ORDER` (10/s, 250/min, 1000/h, 7000/day), `Quota.NON_TRADING` (20/s).
  `DhanTransport._invoke(quota, fn)` acquires the gate before firing; on a DH-904
  it calls `gate.penalize(quota, retry_after)` to back the class off so the next
  acquire waits. `status()` exposes read-only telemetry (used by the pre-deploy
  quota-headroom report). Stdlib-only; injectable `clock`/`sleep` for tests.
- **RetryPolicy** — exponential backoff for transient LTP failures; DH-904 is
  **never** retried (B-011) — it surfaces as a typed `RateLimited` exception that
  propagates up so data reads fail loud instead of silently returning empty/zero
  (K-021, B-005 contract). `is_rate_limited()` normalises Dhan's error text
  into the typed exception at the transport boundary.
- **PositionSyncEngine** — `kernel.sync_positions()` reconciles broker-reported
  positions/balance into the kernel's read models, publishing the same canonical
  `PositionUpdatedEvent` / `BalanceChangedEvent` as an internal fill. Failure-safe:
  a failed fetch keeps the previous state (it never wipes positions or zeroes the
  balance). To distinguish "flat" from "error", `DhanBroker.get_positions` /
  `get_balance` raise on transport failure instead of collapsing to `[]` / `0.0`.

## 7. Object creation (Factory + Flyweight + Registry)

- `SymbolMaster` (flyweight) — same `(kind, symbol, exchange)` resolves to the
  same instance: shared metadata, subscriptions, caches.
- `InstrumentFactory` — one creation entry point bound to a broker.
- `OptionFactory` — synthetic/analytic option construction.
- `BrokerRegistry` — name → factory; `Market(broker="dhan")` / `"paper"`.

## 8. Design patterns used (with justification)

| Pattern       | Where                                        | Why                                 |
|---------------|----------------------------------------------|-------------------------------------|
| Factory/Flyweight | SymbolMaster, InstrumentFactory         | shared instances, single entry point|
| Abstract Factory | BrokerRegistry                          | broker-agnostic object creation     |
| Adapter       | BrokerAdapter → Paper/Dhan                   | normalize broker APIs into domain   |
| Observer      | LiveStream channels                           | decouple tick producers/consumers   |
| Composite     | OptionChain, SyntheticInstrument              | tree of market objects              |
| Facade        | Market                                       | simple public API, hidden infra     |
| State         | TradingSession / MarketState                  | market open/closed/halt behaviour   |
| Capability (open/closed) | BrokerExtensionFacade               | add broker features w/o editing base|
| Decorator     | @capability                                   | compose capabilities onto facade    |
| Immutability  | Quote, Tick, Greeks, MarketDepth (frozen)     | thread-safe market snapshots        |
| Dependency Injection | Instrument(broker=...)                | testability, paper↔live parity      |
| Tell, Don't Ask | rich methods on objects                  | no god services, no static utils    |

## 9. Package organisation

```
ntrade/
  __init__.py          # public API surface
  facade.py            # Market (legacy wrapper over TradingSession)
  factories.py         # InstrumentFactory, OptionFactory
  registry.py          # SymbolMaster (flyweight), BrokerRegistry
  data/                # parallel historical fetch + Parquet storage + gap detection
    __init__.py        # exports ParallelHistoryFetcher, ParquetStorage, GapDetector, ScannerLoader, load_universe
    history_pipeline.py   # ParallelHistoryFetcher (+ fetch_missing)
    parquet_store.py      # ParquetStorage (Hive partition + upsert + duckdb_scan)
    gap_detector.py       # GapDetector
    scanner_loader.py     # ScannerLoader
    universe.py           # load_universe (Nifty CSV → Equity)
  domain/
    session.py         # MarketState, SessionState
    scanner.py         # Scanner / ScannerFacade / ScannerResult
    portfolio.py       # Portfolio / Account read model
    ports.py           # BrokerAdapter ABC + capability registry/facade (ports)
    instruments/       # base, cash, derivatives, chain, capabilities
        base.py         # Instrument (composition root)
        capabilities.py # Market/Stream/Analytics/Derivatives capability objects
    market/            # quote, depth, history, stream, candles
    analytics/         # greeks, indicators, surface
    orders/            # order, book
  brokers/
    base.py            # re-exports BrokerAdapter from domain/ports.py
    capabilities.py    # re-exports capability machinery from domain/ports.py
    paper.py           # PaperBroker (tests/backtest/replay)
    dhan.py            # DhanBroker + @capability extensions
    dhan_auth.py       # token store, cooldown, PIN+TOTP fallback
    dhan_auth_provider.py  # credential provider / auto-refresh
    dhan_mapper.py     # Dhan wire→domain mapping (DhanMapper, chain_from_dhan_df)
    dhan_transport.py  # DhanTransport (BrokerRateGate choke point + retry)
  events/              # canonical event model (market/order/portfolio/risk/lifecycle)
  kernel/              # TradingKernel, ResilientKernel, StrategyRunner, EventBus,
                       # TradingClock, TradingContext, LiveRunner, trading_session
  engines/             # market, candle, indicator, strategy(+strategy_engine), risk,
                       # order (OMS), portfolio, position-sync, strategies (reuse)
  execution/           # ExecutionRouter, SimulatedExecution, BrokerExecution, costs, retry
  storage/             # EventStore (append-only JSONL, recovery_events)
  replay/              # ReplayEngine
  backtest/            # BacktestSimulator, fills (FillPolicy, BarAwareExecution)
  sources/             # MarketFeedSource ABC, SimulatedFeedSource, DhanMarketFeedSource,
                       # SyntheticMarketFeedSource
  sim/                 # tick_simulator (synthesize_1m_ticks, SimTick)
  scanners/            # builtin scanners
  runner/              # LiveRunner (live orchestration harness)
scripts/               # live_runner_run, paper_gate_run, benchmark_latency, backfill_parquet,
                       # download_nifty_universe, live_read_check, pre_deploy_check,
                       # ema_cross_run, benchmark_fetch
tests/                 # ~638 offline test functions across 63 files (kernel/backtest/replay/
                       # source/live/contract suites)
```

## 10. Extensibility guidelines (open/closed)

- **New broker** — subclass `BrokerAdapter`, implement `get_quote`,
  `get_historical`, `place_order`, `get_positions`/`get_balance`/`get_orderbook`;
  add `@capability(name, brokers=("you",))`. Route every REST call through
  `BrokerRateGate` via `_invoke(Quota, fn)`. No domain code changes.
- **New asset class** — subclass `Instrument`, set `KIND` + `DEFAULT_EXCHANGE`;
  factory + flyweight pick it up automatically.
- **New analytics** — pure function over OHLCV in `domain/analytics/indicators.py`,
  add to `compute_bundle` (or as an `Instrument` method delegating there).
- **Consistency principle** — `PaperBroker` implements the same
  `BrokerAdapter` contract as `DhanBroker`, so strategies run identically in
  backtest (paper), replay, and live (Dhan).
- **Zero parity** — strategies are written once against the kernel's events
  (`Strategy` hooks) and run identically in live (BrokerExecution), replay
  (ReplayEngine) and backtest (BacktestSimulator). New event sources just
  publish canonical events.
- **Entry points** — preferred: `TradingSession.connect("dhan")` / `.paper()`
  / `.replay(events)`; legacy: `Market(broker="dhan")`. Both delegate to the
  same `TradingKernel` + `InstrumentFactory` + `BrokerRegistry` internals.

## 11. Known deliberate deviations from the mission sketch

- `history.cached` and `history.indicators` are **properties** (not callables),
  matching the dataframe-like feel; the mission's `cached()` call form is not used.
- `stock.stream` is both the attribute (LiveStream) and callable
  (`stream()` via `LiveStream.__call__`), so both spellings work.
- `stock.tick_stream()` / `stock.candle_stream()` are **methods** (not
  attributes); `stock.ticks()` / `stock.candles()` are the attribute-style
  accessors.
- Option Greeks default to a zero-filled `Greeks()` object rather than `None`,
  so `chain.atm.greeks.delta` never raises.
- `stock.cover()` / `stock.bracket()` are OrderType (`COVER`/`BRACKET`)
  entries, not distinct broker product types — brokers map them (Dhan routes
  BRACKET to `place_super_order`; COVER passes through `order_placement`).

## 12. Knowledge graph (graphify)

The codebase is mapped into a persistent knowledge graph under `graphify-out/`.
Latest run (2026-08-04, from commit `2424757`):

- **6896 nodes · 12321 edges · 393 communities** (91 % EXTRACTED, 9 % INFERRED).
- **God nodes** (most connected): `Equity` (307 edges), `PaperBroker` (172),
  `TradingKernel` (169), `ReplayClock` (164), `DhanTransport` (120), `Instrument`
  (113), `TickEvent` (113), `Option` (109), `TradingSession` (103), `DhanBroker` (92).
- **Key communities**: LiveRunner, EventStore, TradingKernel, BrokerRateGate,
  DhanTransport, Strategy, DhanBroker, TickEvent, PaperBroker, ParquetStorage,
  HistoricalSeries, GapDetector, ScannerLoader, DhanMarketFeedSource,
  SyntheticMarketFeedSource, RiskEngine, ResilientKernel, BrokerExecution.
- **Flagged**: a 4–5-file import cycle in `domain/instruments/`
  (`base → capabilities → chain → {expiry, derivatives} → base`). This is
  deferred hygiene — cycle members are `cached_property`-resolved at call time,
  so it does not cause a module-load circular import, but a future refactor
  should break it for static-analysis cleanliness.
- Run `graphify update .` after code changes (no API cost) to keep the graph
  fresh. See `GRAPH_REPORT.md` in `graphify-out/` for the full community map.
