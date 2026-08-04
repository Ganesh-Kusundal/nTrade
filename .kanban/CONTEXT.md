# nTrade — kanban digest (2026-08-03T17:37:19Z)

nTrade is a quantitative trading platform SDK for Indian markets (NSE/BSE/MCX). It provides an instrument-centric API where every market entity (Equity, Future, Option, Index, Commodity) owns its own state (quote, depth, history, stream) and broker transport is hidden behind a BrokerAdapter. Currently integrated with Dhan via Dhan-Tradehull. Architecture: event-driven kernel with engine pipeline (Market→Candle→Indicator→Strategy→Risk→Portfolio), typed EventBus, execution router supporting live/paper/replay modes. 75 source files, 7269 LOC, 40 test files, 361 passing tests.

## Work in progress
- none

## Blocked
- none

## Planned / backlog
- none

## Recently completed
- F-003 [feature/done] Add universe/CSV loader mapping Nifty 50/100/200/500 CSV symbols to Equity instruments via InstrumentFactory (completed 2026-08-03)
- D-022 [debt/done] Configure duckdb_scan to default to 1-minute data window (start=now-1min, end=now) for live SQL querying (completed 2026-08-03)
- T-041 [task/done] Benchmark ParallelHistoryFetcher.fetch() throughput vs instrument count using PaperBroker seed (completed 2026-08-03)
- B-017 [bug/done] ParquetStorage.upsert crashes (KeyError) on partial-overlap re-fetch — existing[mask] treats MultiIndex difference as column labels, breaks fetch_missing pipeline (completed 2026-08-03)
- T-039 [task/done] Write tests/test_data_layer.py — PaperBroker round trip (fetch → upsert idempotency → gap detect → scanner load → derivative tagging) (completed 2026-08-03)
- T-040 [task/done] Refresh graphify knowledge graph to include ntrade/data layer (completed 2026-08-03)
- D-021 [debt/done] Remove dead Quota import in parquet_store.py (completed 2026-08-03)
- T-038 [task/done] Add pyarrow + duckdb to pyproject.toml deps and install in venv (unblocks ntrade.data import) (completed 2026-08-03)
- F-002 [feature/done] Implement ntrade/data/scanner_loader.py (ScannerLoader.load_universe, partition-pruned 2-3mo reads) (completed 2026-08-03)
- D-018 [debt/done] DhanMarketFeedSource reuses broker tsl instead of second get_tradehull probe chain (completed 2026-08-02)

## Tests
- last pytest run: 0 failing

## Drift since previous scan
- added: ntrade/data/universe.py
- added: scripts/benchmark_fetch.py
- added: tests/test_data_layer.py
- modified: Dependencies\all_instrument 2026-08-03.csv
- modified: ntrade/data/__init__.py
- modified: ntrade/data/parquet_store.py

## Recent commits
- 2424757 review findings sweep: depth retry, LTP failure guard, stale-refresh fix, rate-gate blocked heuristic
- aa7cfe8 K-027 F-001 resample_history labels bars at right edge (CandleEngine parity)
- 50f7a2e K-026 simplify gate._equity_trace zero-qty handling (conditional, keep pop)
- 729e52a K-025 align HistoricalSeries.resample labels with CandleEngine bucketing (label=right)
- 148911b K-024 derive OrderFacade trade_type default from instrument kind
- baf8225 K-023 guard VolumeSpike ratio branch in live mode (unit mismatch)
- 4c7af09 K-022 throttle gap/imbalance scanners symmetrically (30s)
- cb408dd K-021 propagate RateLimited from broker data reads (no silent empty)

(183 uncommitted changes in working tree)

## Architecture & components
- **ntrade/backtest/** (0 files): Backtest simulator: uses TradingKernel in replay mode with SimulatedExecution. Zero parity with live
- **ntrade/brokers/** (0 files): Broker adapter layer: BrokerAdapter (abstract), DhanBroker (Dhan-Tradehull wrapper, 1095 LOC), PaperBroker (deterministic offline), capabilities (dynamic broker-specific extensions via BrokerExtensionFacade)
- **ntrade/domain/instruments/** (0 files): Instrument hierarchy: Instrument (ABC) → Equity, Index, ETF, Currency, Commodity, Bond, Crypto, Spot, Future, Option, SyntheticInstrument. Each owns quote, depth, history, stream, indicators, broker wiring
- **ntrade/domain/market/** (0 files): Market value objects: Quote (frozen), Tick (frozen), MarketDepth (frozen), HistoricalSeries (DataFrame wrapper with cache/fetch/resample), LiveStream (subscription lifecycle + callbacks)
- **ntrade/domain/orders/** (0 files): Order model: Order (mutable dataclass), OrderFacade (fluent API: instrument.order.buy()), OrderSide/OrderType/TradeType/OrderStatus enums
- **ntrade/engines/** (0 files): Engine pipeline: MarketEngine, CandleEngine, IndicatorEngine, StrategyEngine, RiskEngine, PortfolioEngine, PositionSyncEngine, OrderEngine
- **ntrade/events/** (0 files): Typed event hierarchy: Event (base), QuoteUpdatedEvent, TickEvent, OrderPlacedEvent, FillEvent, SignalEvent, RiskHaltedEvent, lifecycle events
- **ntrade/execution/** (0 files): Execution layer: ExecutionRouter (multi-target), BrokerExecution (live), SimulatedExecution (paper), costs module
- **ntrade/facade.py** (1 files): Public entry point: Market class — wraps BrokerRegistry + InstrumentFactory. m.equity('TCS'), m.index('NIFTY'), m.portfolio(), m.account()
- **ntrade/factories.py** (1 files): InstrumentFactory — creates all instrument types with SymbolMaster flyweight cache. OptionFactory for synthetic analytics
- **ntrade/kernel/** (0 files): Core runtime: TradingKernel (engine stack coordinator), EventBus (typed pub/sub), TradingClock (Live/Replay), TradingContext, ResilientKernel (crash recovery + EventStore rebuild)
- **ntrade/registry.py** (1 files): SymbolMaster (flyweight: shared instrument instances), BrokerRegistry (name → factory: dhan, paper)
- **ntrade/replay/** (0 files): Replay engine: feeds recorded events through EventBus with ReplayClock
- **ntrade/runner/** (0 files): Live runner: wraps TradingKernel + MarketFeedSource, closes audit gaps (position sync, order polling, kill switch)
- **ntrade/sources/** (0 files): Market data feeds: DhanFeedSource (WebSocket), MarketFeedSource (kernel feed), SyntheticFeedSource (tick simulation)

## Module dependencies (scalpr, module → imports)
- none

## Knowledge graph (graphify)
- 6863 nodes · 12236 edges · 371 communities (built 2026-08-03T17:17:15Z)
- hubs: Equity(262), PaperBroker(160), TradingKernel(153), ReplayClock(152), DhanTransport(113), TickEvent(108), TradingSession(92), Option(91)
- STALE (3 modified, 235 deleted, 2 new since build) — refresh with `/graphify update`
  - modified: ntrade/data/__init__.py
  - modified: ntrade/data/parquet_store.py
  - modified: tests/test_data_layer.py
- AUTO-REFRESH: run `/graphify update` now, then re-run `kanban.py update` to refresh this digest.

## Data / execution flows
- **market_data**: BrokerAdapter.get_quote() → Quote (frozen) → Instrument.apply_quote() → QuoteUpdatedEvent → EventBus → MarketEngine → CandleEngine → IndicatorEngine → StrategyEngine
- **order_lifecycle**: Instrument.order.buy() → OrderFacade → Order → BrokerAdapter.place_order() → OrderPlacedEvent → EventBus → OrderEngine → RiskEngine → BrokerExecution → FillEvent → PortfolioEngine
- **live_streaming**: BrokerAdapter.subscribe() → LiveStream → tick ingestion → Instrument._quote update → TickEvent/QuoteEvent → EventBus → engines → strategy callbacks
- **position_sync**: PositionSyncEngine pulls broker.get_positions()/get_balance() → reconciles into kernel Portfolio → raises on failure (never silently zeros)
- **replay**: EventStore → timestamped events → ReplayClock.setTime() → EventBus.publish() → identical engine pipeline as live (zero parity)

## Dependencies
- runtime: pandas>=2.0, numpy>=1.24, python-dotenv>=1.0, Dhan-Tradehull>=3.3.2, pyarrow>=14.0, duckdb>=0.9.0
- dev: pytest>=8.0, pytest-timeout>=2.2, pytest-cov>=5.0

## Technical debt & risks
- none

---
*Generated by kanban.cli v1.2.0. Refresh: `python3 .qoder/skills/kanban.cli/scripts/kanban.py update`. Do not edit by hand.*
