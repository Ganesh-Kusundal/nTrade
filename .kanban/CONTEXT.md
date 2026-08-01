# nTrade — kanban digest (2026-08-01T07:14:26Z)

nTrade is a quantitative trading platform SDK for Indian markets (NSE/BSE/MCX). It provides an instrument-centric API where every market entity (Equity, Future, Option, Index, Commodity) owns its own state (quote, depth, history, stream) and broker transport is hidden behind a BrokerAdapter. Currently integrated with Dhan via Dhan-Tradehull. Architecture: event-driven kernel with engine pipeline (Market→Candle→Indicator→Strategy→Risk→Portfolio), typed EventBus, execution router supporting live/paper/replay modes. 75 source files, 7269 LOC, 40 test files, 361 passing tests.

## Work in progress
- none

## Blocked
- none

## Planned / backlog
- none

## Recently completed
- D-002 [debt/done] DhanBroker is 1095 lines mixing auth, mapping, transport, capabilities, normalization (completed 2026-08-01)
- D-003 [debt/done] BrokerAdapter.get_historical() returns pd.DataFrame — leaks pandas at broker boundary (completed 2026-08-01)
- D-004 [debt/done] get_orderbook/get_trade_book return list[dict] — no domain types (completed 2026-08-01)
- D-005 [debt/done] Provider SDK (TradeHull) leaks through capability layer — no transport abstraction (completed 2026-08-01)
- D-006 [debt/done] No rate limiter or retry policy as infrastructure — ad-hoc time.sleep in retry loops (completed 2026-08-01)
- R-001 [risk/done] Phase E (Provider Decomposition) has highest blast radius — 31 capabilities, 8 test files (completed 2026-08-01)
- R-002 [risk/done] Graphify graph is STALE — 25 files deleted since build (completed 2026-08-01)
- D-007 [debt/done] TradingContext shared mutable state has no synchronization — data race in live mode (completed 2026-08-01)
- D-001 [debt/done] Instrument has ~60 methods/properties spanning market data, analytics, streaming, trading, signals — god object (completed 2026-08-01)
- T-001 [task/done] Phase A: Close return-type leaks at broker boundary (OrderBook, TradeBook, IVSurface, GreeksTable) (completed 2026-08-01)

## Tests
- last pytest run: 1 failing (as of 2026-08-01T07:12:46Z)
  - tests/test_indicators.py::test_vwap_between_low_and_high

## Drift since previous scan
- added: ntrade/domain/market/candles.py
- added: ntrade/execution/retry.py
- added: tests/test_candle_series.py
- added: tests/test_retry.py
- modified: ntrade/__init__.py
- modified: ntrade/brokers/base.py
- modified: ntrade/brokers/dhan.py
- modified: ntrade/brokers/dhan_transport.py
- modified: ntrade/brokers/paper.py
- modified: ntrade/domain/market/__init__.py
- modified: ntrade/domain/market/history.py
- modified: ntrade/execution/__init__.py

## Recent commits
- git unavailable

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
- 2160 nodes · 5034 edges · 104 communities (built 2026-07-31T19:32:56Z)
- STALE (59 modified, 25 deleted, 25 new since build) — refresh with `/graphify update`
  - modified: ntrade/__init__.py
  - modified: ntrade/brokers/base.py
  - modified: ntrade/brokers/dhan.py
  - modified: ntrade/brokers/dhan_auth.py
  - modified: ntrade/brokers/paper.py
  - modified: ntrade/domain/instruments/__init__.py
  - modified: ntrade/domain/instruments/base.py
  - modified: ntrade/domain/instruments/chain.py
  - modified: ntrade/domain/instruments/derivatives.py
  - modified: ntrade/domain/market/__init__.py
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
