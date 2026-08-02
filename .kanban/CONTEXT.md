# nTrade — kanban digest (2026-08-02T04:12:41Z)

nTrade is a quantitative trading platform SDK for Indian markets (NSE/BSE/MCX). It provides an instrument-centric API where every market entity (Equity, Future, Option, Index, Commodity) owns its own state (quote, depth, history, stream) and broker transport is hidden behind a BrokerAdapter. Currently integrated with Dhan via Dhan-Tradehull. Architecture: event-driven kernel with engine pipeline (Market→Candle→Indicator→Strategy→Risk→Portfolio), typed EventBus, execution router supporting live/paper/replay modes. 75 source files, 7269 LOC, 40 test files, 361 passing tests.

## Work in progress
- none

## Blocked
- none

## Planned / backlog
- none

## Recently completed
- T-021 [task/done] StrategyRunner full surface or trim to documented API (completed 2026-08-02)
- T-013 [task/done] Wire capability layer into production reads — finish T-002/D-001 (use .market/.history/.stream instead of inst._quote) (completed 2026-08-02)
- T-017 [task/done] Arm ScannerFacade M6 throttle on a built-in scanner (completed 2026-08-02)
- T-019 [task/done] Wire DhanFeed mode matrix (ticker/quote/depth) for the live streaming plan (completed 2026-08-02)
- T-018 [task/done] Wire feed watchdog reconnection (LiveStream reconnect events) — finish R-004 (completed 2026-08-02)
- T-014 [task/done] Attach consumers for R-006 observability events (Heartbeat/FeedDisconnected/OrderTimeout) — watchdog + alerting (completed 2026-08-02)
- T-015 [task/done] Wire DhanAuthProvider.stop() into LiveRunner shutdown path — finish T-011 token refresh (completed 2026-08-02)
- T-016 [task/done] Arm RateLimiter/RetryPolicy in hot paths — finish D-006 resilience infrastructure (completed 2026-08-02)
- T-012 [task/done] Route DhanBroker data calls through DhanTransport — finish T-005/D-005 provider decomposition (completed 2026-08-02)
- T-020 [task/done] Remove fake streaming no-ops (market_feed/order_update_stream stubs) from capability surface (completed 2026-08-01)

## Tests
- last pytest run: 0 failing

## Drift since previous scan
- added: Dependencies/token_1106251237_2026-08-02.txt
- added: Dependencies\all_instrument 2026-08-02.csv
- added: docs/backtest-replay-guide.md
- added: docs/broker-guide.md
- added: docs/how-to-write-a-strategy.md
- added: docs/superpowers/plans/2026-08-02-integration-completeness-batch.md
- modified: .gitignore
- modified: Dependencies/log_files/logs2026-08-01.log
- modified: Dependencies\all_instrument 2026-08-01.csv
- modified: check_connection.py
- modified: docs/superpowers/plans/2026-08-01-findings-hardening.md
- modified: docs/superpowers/plans/2026-08-01-parity-complexity-batch.md
- modified: ntrade/brokers/dhan.py
- modified: ntrade/brokers/dhan_auth.py
- modified: ntrade/brokers/dhan_auth_provider.py
- modified: ntrade/brokers/dhan_mapper.py
- modified: ntrade/brokers/dhan_transport.py
- modified: ntrade/domain/analytics/indicators.py
- modified: ntrade/domain/instruments/__init__.py
- modified: ntrade/domain/instruments/base.py
- … +31 more

## Recent commits
- 66f1bb7 T-021 trim StrategyRunner to the documented add/release surface
- 4aedfc7 T-013 route reads through canonical accessors; retire unused capability facades
- 4c8c3e1 T-017 arm M6 scanner throttle; dedupe Scanner.top ranking
- 362842d T-019 retire speculative feed mode codes; hardcode full data code 21
- 614075f T-018 wire feed watchdog reconnect on disconnect
- 9d899a5 T-014 attach consumers for heartbeat/feed-drop/order-timeout events
- b2cf7df T-015 wire DhanAuthProvider.stop into LiveRunner shutdown
- b2f8f11 T-016 arm RateLimiter in transport and feed hot paths

(184 uncommitted changes in working tree)

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
- 2694 nodes · 7378 edges · 110 communities (built 2026-08-01T15:06:45Z)
- hubs: Equity(245), TradingKernel(154), ReplayClock(150), PaperBroker(147), TickEvent(103), Option(102), TradingSession(95), Instrument(88)
- STALE (25 modified, 3 deleted, 8 new since build) — refresh with `/graphify update`
  - modified: ntrade/brokers/dhan.py
  - modified: ntrade/brokers/dhan_mapper.py
  - modified: ntrade/brokers/dhan_transport.py
  - modified: ntrade/domain/instruments/__init__.py
  - modified: ntrade/domain/instruments/base.py
  - modified: ntrade/domain/instruments/capabilities.py
  - modified: ntrade/domain/scanner.py
  - modified: ntrade/engines/market_engine.py
  - modified: ntrade/engines/risk_engine.py
  - modified: ntrade/kernel/runner.py
- AUTO-REFRESH: run `/graphify update` now, then re-run `kanban.py update` to refresh this digest.

## Data / execution flows
- **market_data**: BrokerAdapter.get_quote() → Quote (frozen) → Instrument.apply_quote() → QuoteUpdatedEvent → EventBus → MarketEngine → CandleEngine → IndicatorEngine → StrategyEngine
- **order_lifecycle**: Instrument.order.buy() → OrderFacade → Order → BrokerAdapter.place_order() → OrderPlacedEvent → EventBus → OrderEngine → RiskEngine → BrokerExecution → FillEvent → PortfolioEngine
- **live_streaming**: BrokerAdapter.subscribe() → LiveStream → tick ingestion → Instrument._quote update → TickEvent/QuoteEvent → EventBus → engines → strategy callbacks
- **position_sync**: PositionSyncEngine pulls broker.get_positions()/get_balance() → reconciles into kernel Portfolio → raises on failure (never silently zeros)
- **replay**: EventStore → timestamped events → ReplayClock.setTime() → EventBus.publish() → identical engine pipeline as live (zero parity)

## Dependencies
- runtime: pandas>=2.0, numpy>=1.24, python-dotenv>=1.0, Dhan-Tradehull>=3.3.2
- dev: pytest>=8.0

## Technical debt & risks
- none

---
*Generated by kanban.cli v1.2.0. Refresh: `python3 .qoder/skills/kanban.cli/scripts/kanban.py update`. Do not edit by hand.*
