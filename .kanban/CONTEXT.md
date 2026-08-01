# nTrade — kanban digest (2026-08-01T13:16:58Z)

nTrade is a quantitative trading platform SDK for Indian markets (NSE/BSE/MCX). It provides an instrument-centric API where every market entity (Equity, Future, Option, Index, Commodity) owns its own state (quote, depth, history, stream) and broker transport is hidden behind a BrokerAdapter. Currently integrated with Dhan via Dhan-Tradehull. Architecture: event-driven kernel with engine pipeline (Market→Candle→Indicator→Strategy→Risk→Portfolio), typed EventBus, execution router supporting live/paper/replay modes. 75 source files, 7269 LOC, 40 test files, 361 passing tests.

## Work in progress
- none

## Blocked
- none

## Planned / backlog
- none

## Recently completed
- T-011 [task/done] Auto-refresh Dhan token on expiry — proactive buffer + background timer + _ensure_tsl() in critical paths (completed 2026-08-01)
- B-009 [bug/done] HF-001 MarketEngine.on_tick broadcasts read-model _quote.ltp instead of event.price (stale 0.0 for depth-kind ticks; fragile ordering dependence) (completed 2026-08-01)
- D-017 [debt/done] gate.py re-derives equity/drawdown with divergent MTM vs RiskEngine.equity/Position.market_value (completed 2026-08-01)
- B-006 [bug/done] F-004 live fills carry zero commission/statutory — parity gap (completed 2026-08-01)
- B-007 [bug/done] F-005 backtest candles degenerate (open=high=low=close) — QuoteEvent OHLCV ignored by CandleEngine (completed 2026-08-01)
- B-008 [bug/done] Scanner reads indicator keys pipeline never produces (avg_volume/rsi/supertrend vs rsi_14/atr_14/stx_10_3) — 3 dead branches (completed 2026-08-01)
- D-002 [debt/done] DhanBroker is 1095 lines mixing auth, mapping, transport, capabilities, normalization (completed 2026-08-01)
- D-003 [debt/done] BrokerAdapter.get_historical() returns pd.DataFrame — leaks pandas at broker boundary (completed 2026-08-01)
- D-004 [debt/done] get_orderbook/get_trade_book return list[dict] — no domain types (completed 2026-08-01)
- D-005 [debt/done] Provider SDK (TradeHull) leaks through capability layer — no transport abstraction (completed 2026-08-01)

## Tests
- last pytest run: 1 failing (as of 2026-08-01T13:15:43Z)
  - tests/test_indicators.py::test_vwap_between_low_and_high

## Drift since previous scan
- added: docs/superpowers/plans/2026-08-01-parity-complexity-batch.md
- added: tests/test_candle_engine.py
- modified: Dependencies/log_files/logs2026-08-01.log
- modified: Dependencies\all_instrument 2026-08-01.csv
- modified: ntrade/domain/analytics/indicators.py
- modified: ntrade/engines/candle_engine.py
- modified: ntrade/engines/market_engine.py
- modified: ntrade/execution/broker_executor.py
- modified: ntrade/kernel/session.py
- modified: ntrade/runner/gate.py
- modified: ntrade/scanners/builtin.py
- modified: tests/test_indicators.py
- modified: tests/test_kernel_engines.py
- modified: tests/test_live_execution.py
- modified: tests/test_paper_gate.py
- modified: tests/test_scanner.py

## Recent commits
- a3e9f28 Final review: guard BreakoutScanner stx type; gate zero-peak + docstring; scanner integration test
- 2c2d4e3 D-017 gate equity: settle at balance events (no pre-fill spike)
- 7e38917 H6 task-5 report: gate equity derives from portfolio read-model events (D-017)
- 8cbf69a gate equity derives from portfolio read-model events (D-017)
- 88572d9 H6 task-3 report: scanners read canonical indicator keys (B-008)
- 42cbbf5 scanners read canonical indicator keys; emit avg_volume (B-008)
- 954c20e B-009 on_tick broadcasts the tick's own price (HF-001)
- be18e1d F-005 review: document bar-authoritative tick-skip; drop unused import

(25 uncommitted changes in working tree)

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
- 2824 nodes · 7004 edges · 129 communities (built 2026-08-01T07:34:33Z)
- hubs: Equity(224), PaperBroker(147), TradingKernel(144), ReplayClock(132), TickEvent(98), Instrument(88), Option(87), TradingSession(83)
- STALE (44 modified, 25 deleted, 8 new since build) — refresh with `/graphify update`
  - modified: ntrade/__init__.py
  - modified: ntrade/backtest/simulator.py
  - modified: ntrade/brokers/base.py
  - modified: ntrade/brokers/dhan.py
  - modified: ntrade/brokers/dhan_auth.py
  - modified: ntrade/brokers/dhan_auth_provider.py
  - modified: ntrade/brokers/dhan_mapper.py
  - modified: ntrade/brokers/dhan_transport.py
  - modified: ntrade/brokers/paper.py
  - modified: ntrade/domain/analytics/greeks.py
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
