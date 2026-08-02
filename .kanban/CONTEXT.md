# nTrade — kanban digest (2026-08-02T17:17:05Z)

nTrade is a quantitative trading platform SDK for Indian markets (NSE/BSE/MCX). It provides an instrument-centric API where every market entity (Equity, Future, Option, Index, Commodity) owns its own state (quote, depth, history, stream) and broker transport is hidden behind a BrokerAdapter. Currently integrated with Dhan via Dhan-Tradehull. Architecture: event-driven kernel with engine pipeline (Market→Candle→Indicator→Strategy→Risk→Portfolio), typed EventBus, execution router supporting live/paper/replay modes. 75 source files, 7269 LOC, 40 test files, 361 passing tests.

## Work in progress
- none

## Blocked
- none

## Planned / backlog
- none

## Recently completed
- T-035 [task/done] Gate DhanMarketFeedSource login probe (_context_from_env -> get_tradehull) through session BrokerRateGate (completed 2026-08-02)
- T-036 [task/done] Expose BrokerRateGate.status() telemetry + quota headroom row in live_read_check.py (completed 2026-08-02)
- T-037 [task/done] Regression test: PositionSyncEngine sync pays NON_TRADING quota via transport gate (completed 2026-08-02)
- T-026 [task/done] Route every DhanTransport _tsl call through _invoke(quota, fn); drop 10/s LTP-only limiter (completed 2026-08-02)
- B-010 [bug/done] Move DhanBroker self.tsl order/status paths onto throttled transport (completed 2026-08-02)
- T-027 [task/done] Integration tests: Quote 1/s, shared gate, order path, penalize on DH-904 (completed 2026-08-02)
- T-028 [task/done] Route capability advanced-order placement through ORDER gate (place_super/slice/forever/conditional_trigger, cancel_all) (completed 2026-08-02)
- T-029 [task/done] Route capability order queries/modify/cancel through ORDER gate (get/modify/cancel super & forever, exchange_time) (completed 2026-08-02)
- T-030 [task/done] Route conditional-trigger lifecycle through ORDER gate (get_all/get_by_id/delete_conditional_trigger) (completed 2026-08-02)
- T-031 [task/done] Route kill_switch + enable_pnl_based_exit + margin_calculator through NON_TRADING gate (completed 2026-08-02)

## Tests
- last pytest run: 0 failing

## Drift since previous scan
- modified: Dependencies/log_files/logs2026-08-02.log
- modified: Dependencies\all_instrument 2026-08-02.csv
- modified: ntrade/brokers/dhan.py
- modified: ntrade/execution/rate_limit.py
- modified: ntrade/sources/dhan_feed.py
- modified: scripts/live_read_check.py
- modified: test.ipynb
- modified: tests/test_dhan_feed.py
- modified: tests/test_pre_deploy_check.py
- modified: tests/test_rate_gate_integration.py
- modified: tests/test_rate_limit.py

## Recent commits
- 4741c11 T-037 regression test: position sync pays NON_TRADING quota
- 8eacfc9 T-036 expose BrokerRateGate.status() telemetry + quota headroom in live-read
- dfe1b4e T-035 gate DhanMarketFeedSource login probe through BrokerRateGate
- 8bd7b09 T-034 fail closed on DEGRADED live reads
- 7ff8f90 T-033 add unified pre-deploy gate script
- 9e284d0 B-012 gate auth probe reads through BrokerRateGate
- 925ccea T-027 integration tests for broker rate gate
- 4f58e25 B-010 route order paths through throttled transport; drop 10/s limiter

(178 uncommitted changes in working tree)

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
- 6520 nodes · 11043 edges · 391 communities (built 2026-08-02T10:05:57Z)
- STALE (12 modified, 311 deleted, 8 new since build) — refresh with `/graphify update`
  - modified: ntrade/brokers/dhan.py
  - modified: ntrade/brokers/dhan_auth.py
  - modified: ntrade/brokers/dhan_auth_provider.py
  - modified: ntrade/brokers/dhan_transport.py
  - modified: ntrade/execution/__init__.py
  - modified: ntrade/execution/retry.py
  - modified: ntrade/sources/dhan_feed.py
  - modified: scripts/live_read_check.py
  - modified: tests/test_dhan_auth_unit.py
  - modified: tests/test_dhan_broker.py
- AUTO-REFRESH: run `/graphify update` now, then re-run `kanban.py update` to refresh this digest.

## Data / execution flows
- **market_data**: BrokerAdapter.get_quote() → Quote (frozen) → Instrument.apply_quote() → QuoteUpdatedEvent → EventBus → MarketEngine → CandleEngine → IndicatorEngine → StrategyEngine
- **order_lifecycle**: Instrument.order.buy() → OrderFacade → Order → BrokerAdapter.place_order() → OrderPlacedEvent → EventBus → OrderEngine → RiskEngine → BrokerExecution → FillEvent → PortfolioEngine
- **live_streaming**: BrokerAdapter.subscribe() → LiveStream → tick ingestion → Instrument._quote update → TickEvent/QuoteEvent → EventBus → engines → strategy callbacks
- **position_sync**: PositionSyncEngine pulls broker.get_positions()/get_balance() → reconciles into kernel Portfolio → raises on failure (never silently zeros)
- **replay**: EventStore → timestamped events → ReplayClock.setTime() → EventBus.publish() → identical engine pipeline as live (zero parity)

## Dependencies
- unavailable

## Technical debt & risks
- none

---
*Generated by kanban.cli v1.2.0. Refresh: `python3 .qoder/skills/kanban.cli/scripts/kanban.py update`. Do not edit by hand.*
