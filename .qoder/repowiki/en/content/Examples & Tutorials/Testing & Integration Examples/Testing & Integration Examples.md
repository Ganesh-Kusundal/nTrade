# Testing & Integration Examples

<cite>
**Referenced Files in This Document**
- [test_integration_pipeline.py](file://tests/test_integration_pipeline.py)
- [test_replay_backtest.py](file://tests/test_replay_backtest.py)
- [test_engine_pipeline.py](file://tests/test_engine_pipeline.py)
- [test_live_execution.py](file://tests/test_live_execution.py)
- [test_broker_executor.py](file://tests/test_broker_executor.py)
- [test_brokers.py](file://tests/test_brokers.py)
- [test_synthetic_feed.py](file://tests/test_synthetic_feed.py)
- [test_indicators.py](file://tests/test_indicators.py)
- [test_portfolio_account.py](file://tests/test_portfolio_account.py)
- [test_risk_breakers.py](file://tests/test_risk_breakers.py)
- [test_strategy_runner.py](file://tests/test_strategy_runner.py)
- [test_kernel_engines.py](file://tests/test_kernel_engines.py)
- [test_benchmark.py](file://tests/test_benchmark.py)
- [test_gap_closure.py](file://tests/test_gap_closure.py)
- [test_rate_limit.py](file://tests/test_rate_limit.py)
- [benchmark_latency.py](file://scripts/benchmark_latency.py)
</cite>

## Update Summary
**Changes Made**
- Added comprehensive section on gap closure testing with rate limiting exception propagation
- Updated broker adapter testing patterns to include executed_price() and executed_price_and_time() methods
- Enhanced regression testing examples for error handling scenarios
- Added detailed coverage of rate limiting behavior in order lifecycle operations

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Gap Closure Testing and Rate Limiting](#gap-closure-testing-and-rate-limiting)
7. [Dependency Analysis](#dependency-analysis)
8. [Performance Considerations](#performance-considerations)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Conclusion](#conclusion)
11. [Appendices](#appendices)

## Introduction
This document provides comprehensive testing examples and integration patterns for nTrade, focusing on end-to-end workflows with mock brokers and synthetic data sources, unit tests for strategies and risk rules, replay-based and deterministic testing, performance benchmarking, load and stress testing methodologies, and best practices for test organization and continuous integration. It synthesizes the repository's existing tests to demonstrate how to validate market data ingestion through order execution, portfolio updates, and broker reconciliation. The latest additions include comprehensive gap closure testing with proper rate limiting exception propagation patterns.

## Project Structure
The testing suite is organized by feature area:
- End-to-end pipeline and replay/backtest tests
- Broker layer and execution path tests
- Kernel engines (market, candle, indicator) tests
- Risk engine and circuit breaker tests
- Strategy runner and multi-strategy isolation tests
- Synthetic feed reconstruction and tick generation tests
- Portfolio/account domain object tests
- Performance benchmarking utilities and scripts
- **New**: Gap closure testing with rate limiting exception propagation

```mermaid
graph TB
subgraph "Tests"
A["Integration Pipeline"]
B["Replay & Backtest"]
C["Engine Pipeline"]
D["Live Execution"]
E["Broker Executor"]
F["Brokers"]
G["Synthetic Feed"]
H["Indicators"]
I["Portfolio/Account"]
J["Risk Breakers"]
K["Strategy Runner"]
L["Kernel Engines"]
M["Benchmark"]
N["Gap Closure Testing"]
O["Rate Limit Testing"]
end
A --> C
B --> C
C --> D
D --> E
F --> D
G --> A
H --> L
I --> C
J --> C
K --> C
L --> C
M --> C
N --> F
O --> N
```

[No sources needed since this diagram shows conceptual workflow, not actual code structure]

## Core Components
Key components validated across tests include:
- TradingKernel orchestration of engines and event bus
- Market, Candle, Indicator, Risk, Order, and Portfolio engines
- Broker adapters (PaperBroker, DhanBroker via stubs)
- ReplayEngine and EventStore for deterministic replay
- SyntheticMarketFeedSource for reproducible tick streams
- StrategyRunner for multi-strategy lifecycle and per-strategy risk
- BacktestSimulator and FillPolicy for bar-aware fills
- **New**: Comprehensive gap closure testing with rate limiting exception propagation

**Section sources**
- [test_engine_pipeline.py:1-137](file://tests/test_engine_pipeline.py#L1-L137)
- [test_replay_backtest.py:1-267](file://tests/test_replay_backtest.py#L1-L267)
- [test_live_execution.py:1-447](file://tests/test_live_execution.py#L1-L447)
- [test_broker_executor.py:1-39](file://tests/test_broker_executor.py#L1-L39)
- [test_brokers.py:1-107](file://tests/test_brokers.py#L1-L107)
- [test_synthetic_feed.py:1-63](file://tests/test_synthetic_feed.py#L1-L63)
- [test_indicators.py:1-149](file://tests/test_indicators.py#L1-L149)
- [test_portfolio_account.py:1-56](file://tests/test_portfolio_account.py#L1-L56)
- [test_risk_breakers.py:1-111](file://tests/test_risk_breakers.py#L1-L111)
- [test_strategy_runner.py:1-243](file://tests/test_strategy_runner.py#L1-L243)
- [test_kernel_engines.py:1-102](file://tests/test_kernel_engines.py#L1-L102)
- [test_benchmark.py:1-15](file://tests/test_benchmark.py#L1-L15)
- [test_gap_closure.py:1-593](file://tests/test_gap_closure.py#L1-L593)
- [test_rate_limit.py:1-237](file://tests/test_rate_limit.py#L1-L237)

## Architecture Overview
The kernel orchestrates a pipeline from market data ingestion to order execution and portfolio updates. Tests demonstrate both simulated and live paths with zero-parity guarantees.

```mermaid
sequenceDiagram
participant Feed as "SyntheticMarketFeedSource"
participant Kernel as "TradingKernel"
participant MarketEng as "MarketEngine"
participant CandleEng as "CandleEngine"
participant Strat as "Strategy"
participant Risk as "RiskEngine"
participant OMS as "OrderEngine"
participant Exec as "BrokerExecution"
participant Broker as "PaperBroker/DhanBroker(stub)"
participant Port as "Portfolio"
Feed->>Kernel : publish TickEvent(s)
Kernel->>MarketEng : update instrument quote
MarketEng-->>Kernel : QuoteUpdatedEvent
Kernel->>CandleEng : aggregate ticks into candles
CandleEng-->>Kernel : CandleClosedEvent
Kernel->>Strat : on_candle_closed/on_tick
Strat-->>Kernel : emit_signal()
Kernel->>Risk : screen signal
Risk-->>Kernel : SignalApprovedEvent or SignalRejectedEvent
Kernel->>OMS : create order intent
OMS->>Exec : place order
Exec->>Broker : order_placement/get_order_status
Broker-->>Exec : status/fill details
Exec-->>Kernel : OrderAccepted/OrderFilled/OrderRejected
Kernel->>Port : update positions and balance
```

**Diagram sources**
- [test_integration_pipeline.py:46-96](file://tests/test_integration_pipeline.py#L46-L96)
- [test_engine_pipeline.py:65-101](file://tests/test_engine_pipeline.py#L65-L101)
- [test_live_execution.py:75-124](file://tests/test_live_execution.py#L75-L124)

## Detailed Component Analysis

### End-to-End Workflow Testing with Mock Brokers and Synthetic Data
- Use SyntheticMarketFeedSource to generate deterministic OHLCV-derived ticks that reconstruct bars exactly.
- Wire PaperBroker into TradingKernel for simulated execution without network calls.
- Assert full pipeline events: quotes updated, candles closed, signals emitted and approved, orders accepted/filled, portfolio updated.

```mermaid
flowchart TD
Start(["Start Test"]) --> BuildKernel["Create TradingKernel with PaperBroker"]
BuildKernel --> RegisterInstrument["Register Equity instrument"]
RegisterInstrument --> AttachFeed["Attach SyntheticMarketFeedSource"]
AttachFeed --> StartKernel["kernel.start(), source.start()"]
StartKernel --> PublishTicks["Publish OHLCV-derived ticks"]
PublishTicks --> ValidateQuote["Assert instrument.ltp > 0"]
ValidateQuote --> FlushCandles["Flush candle engine"]
FlushCandles --> AssertCandles["Assert >=1 closed candle"]
AssertCandles --> AssertSignals["Assert SignalGeneratedEvent present"]
AssertSignals --> AssertApproved["Assert SignalApprovedEvent present"]
AssertApproved --> AssertPortfolio["Assert balance decreased after BUY fill"]
AssertPortfolio --> End(["Stop kernel and finish"])
```

**Diagram sources**
- [test_integration_pipeline.py:46-96](file://tests/test_integration_pipeline.py#L46-L96)

**Section sources**
- [test_integration_pipeline.py:46-96](file://tests/test_integration_pipeline.py#L46-L96)
- [test_synthetic_feed.py:38-63](file://tests/test_synthetic_feed.py#L38-L63)

### Unit Testing Custom Strategies
- Implement minimal strategies emitting signals on first tick or candle close.
- Verify single emission behavior, side/quantity/price propagation, and netting effects when paired with opposite-side signals.
- Use ReplayClock for deterministic time progression.

```mermaid
classDiagram
class Strategy {
+name
+on_tick(event)
+on_candle_closed(event)
+emit_signal(symbol, exchange, side, quantity, price)
}
class BuyOnTick {
+on_tick(event)
-emitted bool
}
class SellOnSecondTick {
+on_tick(event)
-count int
}
Strategy <|-- BuyOnTick
Strategy <|-- SellOnSecondTick
```

**Diagram sources**
- [test_engine_pipeline.py:16-47](file://tests/test_engine_pipeline.py#L16-L47)

**Section sources**
- [test_engine_pipeline.py:16-47](file://tests/test_engine_pipeline.py#L16-L47)

### Broker Adapters and Execution Path
- PaperBroker supports seeding quotes, historical data, and order book depth; validates capability extension pattern.
- DhanBroker integration tested via stubbed Tradehull methods to simulate placement, polling, partial fills, rejections, and idempotent poll behavior.
- BrokerExecution handles stale order eviction, timeout detection, and fallback order IDs.

```mermaid
sequenceDiagram
participant Strat as "Strategy"
participant Kernel as "TradingKernel"
participant Exec as "BrokerExecution"
participant Broker as "DhanBroker(stub)"
Strat-->>Kernel : emit_signal(BUY, qty=5, price=100)
Kernel->>Exec : place order
Exec->>Broker : order_placement()
Broker-->>Exec : order_id="ORD-100"
Exec-->>Kernel : OrderAcceptedEvent
Kernel->>Exec : poll_orders()
Exec->>Broker : get_order_status()/get_order_detail()
Broker-->>Exec : COMPLETE, filledQty=5, avgPrice=100
Exec-->>Kernel : OrderFilledEvent
Kernel->>Kernel : sync_positions() if needed
```

**Diagram sources**
- [test_live_execution.py:75-124](file://tests/test_live_execution.py#L75-L124)
- [test_broker_executor.py:13-39](file://tests/test_broker_executor.py#L13-L39)

**Section sources**
- [test_brokers.py:10-107](file://tests/test_brokers.py#L10-L107)
- [test_live_execution.py:75-124](file://tests/test_live_execution.py#L75-L124)
- [test_broker_executor.py:13-39](file://tests/test_broker_executor.py#L13-L39)

### Risk Rules and Circuit Breakers
- RiskEngine enforces daily loss limits, drawdown thresholds, price deviation checks, and allowlists.
- Tests assert halting/resuming behavior, idempotent check(), and rejection reasons.

```mermaid
flowchart TD
S["SignalReceived"] --> CheckDailyLoss{"Daily loss exceeded?"}
CheckDailyLoss --> |Yes| Halt["Halt and reject all signals"]
CheckDailyLoss --> |No| CheckDrawdown{"Drawdown exceeded?"}
CheckDrawdown --> |Yes| Halt
CheckDrawdown --> |No| CheckDeviation{"Price deviation ok?"}
CheckDeviation --> |No| Reject["Reject signal"]
CheckDeviation --> |Yes| Approve["Approve signal"]
Halt --> EmitHalted["Emit RiskHaltedEvent"]
Reject --> EmitRejected["Emit SignalRejectedEvent"]
Approve --> EmitApproved["Emit SignalApprovedEvent"]
```

**Diagram sources**
- [test_risk_breakers.py:30-111](file://tests/test_risk_breakers.py#L30-L111)

**Section sources**
- [test_risk_breakers.py:30-111](file://tests/test_risk_breakers.py#L30-L111)

### Replay-Based and Deterministic Testing
- EventStore persists and replays events chronologically, supporting JSONL roundtrip and nested events.
- ReplayEngine drives TradingKernel deterministically using ReplayClock; zero-parity between simulated and live paths verified.
- BacktestSimulator produces equity curves, commissions, drawdown metrics, and bar-aware fills.

```mermaid
sequenceDiagram
participant Store as "EventStore"
participant Engine as "ReplayEngine"
participant Kernel as "TradingKernel"
participant Strat as "Strategy"
Store-->>Engine : replay() iterator
Engine->>Kernel : run(events)
Kernel->>Strat : dispatch events
Strat-->>Kernel : emit_signal()
Kernel-->>Store : append(OrderFilledEvent etc.)
Engine-->>Kernel : advance clock per event ts
```

**Diagram sources**
- [test_replay_backtest.py:24-41](file://tests/test_replay_backtest.py#L24-41)
- [test_replay_backtest.py:72-99](file://tests/test_replay_backtest.py#L72-99)

**Section sources**
- [test_replay_backtest.py:24-41](file://tests/test_replay_backtest.py#L24-41)
- [test_replay_backtest.py:72-99](file://tests/test_replay_backtest.py#L72-99)
- [test_replay_backtest.py:133-182](file://tests/test_replay_backtest.py#L133-182)

### Performance Benchmarking, Load, and Stress Testing
- measure_tick_throughput provides ticks, events_per_sec, wall_seconds for kernel throughput measurement.
- CLI script writes latency results to .benchmarks/latency.json for CI tracking.
- Load/stress patterns can be built by increasing n_ticks and asserting stable throughput and latency bounds.

```mermaid
flowchart TD
StartBench["Start Benchmark"] --> CreateKernel["Create TradingKernel(mode=replay)"]
CreateKernel --> Measure["measure_tick_throughput(k, n_ticks)"]
Measure --> Stats["Collect stats dict"]
Stats --> WriteOut["Write JSON to .benchmarks/latency.json"]
WriteOut --> EndBench(["End Benchmark"])
```

**Diagram sources**
- [test_benchmark.py:9-15](file://tests/test_benchmark.py#L9-15)
- [benchmark_latency.py:18-32](file://scripts/benchmark_latency.py#L18-32)

**Section sources**
- [test_benchmark.py:9-15](file://tests/test_benchmark.py#L9-15)
- [benchmark_latency.py:18-32](file://scripts/benchmark_latency.py#L18-32)

### Testing Custom Indicators
- Validate RSI bounds and trend direction, ATR positivity, VWAP within session range, Supertrend signals, SMA/EMA warm-up periods, and compute_bundle outputs.
- IndicatorEngine respects max_rows to bound memory usage.

```mermaid
classDiagram
class IndicatorEngine {
+on_candle_closed(event)
-_rows dict
+max_rows int
}
class Bundle {
+rsi_14 float
+atr_14 float
+vwap float
+stx_10_3 string
+ema_9 float
+ema_21 float
+sma_20 float
}
IndicatorEngine --> Bundle : "computes"
```

**Diagram sources**
- [test_indicators.py:129-149](file://tests/test_indicators.py#L129-149)

**Section sources**
- [test_indicators.py:23-127](file://tests/test_indicators.py#L23-127)
- [test_indicators.py:129-149](file://tests/test_indicators.py#L129-149)

### Order Routing Logic and Portfolio Calculations
- Portfolio and Account objects support PnL, market value, holdings lookup, and broker-backed initialization.
- Tests verify position netting, balance changes, and canonical events emitted during updates.

```mermaid
erDiagram
POSITION {
string symbol PK
float quantity
float avg_price
float ltp
float pnl
float market_value
}
ACCOUNT {
float balance
}
PORTFOLIO {
float pnl
float market_value
}
POSITION ||--o{ PORTFOLIO : contributes_to
ACCOUNT ||--o{ PORTFOLIO : funds_trading
```

**Diagram sources**
- [test_portfolio_account.py:9-56](file://tests/test_portfolio_account.py#L9-56)

**Section sources**
- [test_portfolio_account.py:9-56](file://tests/test_portfolio_account.py#L9-56)

### Strategy Runner and Multi-Strategy Isolation
- StrategyRunner manages multiple strategies with unique names, hot detach/enable/disable, per-strategy risk limits, and global risk pause management.
- Verifies that per-strategy caps do not interfere across strategies and that release restores global risk handlers.

```mermaid
sequenceDiagram
participant Runner as "StrategyRunner"
participant Kernel as "TradingKernel"
participant S1 as "Strategy A"
participant S2 as "Strategy B"
participant Risk as "RiskEngine"
Runner->>Kernel : add(S1), add(S2)
Kernel->>S1 : on_tick -> emit_signal
Kernel->>S2 : on_tick -> emit_signal
Runner->>Risk : apply per-strategy risk overrides
Kernel-->>Runner : status report
Runner->>Kernel : remove/disable/enable
Runner->>Kernel : release (restore global risk)
```

**Diagram sources**
- [test_strategy_runner.py:72-107](file://tests/test_strategy_runner.py#L72-107)
- [test_strategy_runner.py:109-175](file://tests/test_strategy_runner.py#L109-175)

**Section sources**
- [test_strategy_runner.py:72-107](file://tests/test_strategy_runner.py#L72-107)
- [test_strategy_runner.py:109-175](file://tests/test_strategy_runner.py#L109-175)

### Kernel Engines: Market, Candle, Indicator
- MarketEngine projects ticks/quotes/depth onto instruments and stream state.
- CandleEngine closes candles on bucket transitions and flushes partials.
- IndicatorEngine publishes bundles after sufficient candles and updates instrument analytics.

```mermaid
flowchart TD
Tick["TickEvent"] --> Market["MarketEngine.update()"]
Market --> QuoteUpdate["QuoteUpdatedEvent"]
Tick --> Candle["CandleEngine.aggregate()"]
Candle --> Close["CandleClosedEvent"]
Close --> Indicator["IndicatorEngine.compute_bundle()"]
Indicator --> Bundle["IndicatorUpdatedEvent"]
```

**Diagram sources**
- [test_kernel_engines.py:27-102](file://tests/test_kernel_engines.py#L27-102)

**Section sources**
- [test_kernel_engines.py:27-102](file://tests/test_kernel_engines.py#L27-102)

## Gap Closure Testing and Rate Limiting

**Updated** Added comprehensive regression test examples demonstrating proper rate limiting exception propagation testing in gap closure scenarios. The new tests provide valuable examples of testing executed_price() and executed_price_and_time() methods with mock brokers and various error conditions.

### Gap Closure Testing Patterns
The gap closure testing suite covers critical order lifecycle scenarios across Paper and Dhan brokers with comprehensive offline testing using stubbed Tradehull implementations.

```mermaid
flowchart TD
Start(["Gap Closure Test"]) --> CreateBroker["Create Mock Broker"]
CreateBroker --> SetupOrder["Setup Order with Status"]
SetupOrder --> TestCancel["Test Cancel Operations"]
SetupOrder --> TestModify["Test Modify Operations"]
SetupOrder --> TestRefresh["Test Refresh Operations"]
TestCancel --> ValidateStatus["Validate Status Changes"]
TestModify --> ValidateParams["Validate Parameter Propagation"]
TestRefresh --> ValidateFill["Validate Fill Details"]
ValidateStatus --> TestBooks["Test Order/Trade Books"]
ValidateParams --> TestBooks
ValidateFill --> TestBooks
TestBooks --> TestCapabilities["Test Broker Capabilities"]
TestCapabilities --> TestAnalytics["Test Analytics Functions"]
TestAnalytics --> End(["Complete"])
```

**Diagram sources**
- [test_gap_closure.py:50-183](file://tests/test_gap_closure.py#L50-183)

### Rate Limiting Exception Propagation Testing

**Critical Pattern**: Rate limiting exceptions must propagate properly rather than being silently swallowed, preventing false positive fills at 0.0 prices.

```mermaid
sequenceDiagram
participant Order as "Order.executed_price()"
participant Broker as "DhanBroker.get_executed_price()"
participant Transport as "DhanTransport"
participant Gate as "BrokerRateGate"
Order->>Broker : get_executed_price(order)
Broker->>Transport : get_executed_price(order_id)
Transport->>Gate : acquire(Quota.ORDER)
alt Rate Limited
Gate-->>Transport : RateLimited exception
Transport-->>Broker : RateLimited exception
Broker-->>Order : RateLimited exception (propagated)
else Network Error
Transport-->>Broker : ConnectionError
Broker-->>Order : Return avg_price fallback
else Success
Transport-->>Broker : price value
Broker-->>Order : price value
end
```

**Diagram sources**
- [test_gap_closure.py:454-485](file://tests/test_gap_closure.py#L454-485)
- [ntrade/brokers/dhan.py:386-403](file://ntrade/brokers/dhan.py#L386-403)

### Key Testing Scenarios

#### Rate Limited Exception Propagation
Tests ensure that DH-904 rate limit errors are properly propagated rather than being masked as successful responses:

- `test_executed_price_propagates_rate_limited`: Verifies RateLimited exceptions are raised, not returned as 0.0
- `test_executed_price_and_time_propagates_rate_limited`: Ensures price+time pair also propagates rate limits
- `test_executed_price_degrades_on_other_errors`: Non-rate errors fall back to avg_price instead of raising
- `test_executed_price_and_time_degrades_on_other_errors`: Same degrade contract for price+time pairs

#### Comprehensive Broker Capability Testing
The gap closure tests cover extensive broker capabilities including:

- **Order Lifecycle**: cancel, modify, refresh operations for both Paper and Dhan brokers
- **Book Management**: orderbook and tradebook functionality with DataFrame normalization
- **Advanced Orders**: super orders, slice orders, conditional triggers, forever orders
- **Analytics**: Heikin-Ashi transforms, Renko bricks, and instrument analytics
- **Market Data**: expiry lists, option chains, lot sizes, historical data

#### Mock Broker Construction Pattern
The tests use a sophisticated mock broker construction pattern:

```python
def make_broker(**tsl_methods):
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(**tsl_methods)
    broker._transport = DhanTransport(broker.tsl)
    return broker
```

This allows testing Dhan-specific functionality without actual network calls while maintaining proper type checking and method signatures.

**Section sources**
- [test_gap_closure.py:22-47](file://tests/test_gap_closure.py#L22-47)
- [test_gap_closure.py:454-521](file://tests/test_gap_closure.py#L454-521)
- [test_gap_closure.py:128-183](file://tests/test_gap_closure.py#L128-183)
- [test_gap_closure.py:358-397](file://tests/test_gap_closure.py#L358-397)

## Dependency Analysis
- TradingKernel depends on engines (Market, Candle, Indicator, Risk, Order, Portfolio) and optional broker adapter.
- ReplayEngine depends on EventStore and ReplayClock; BacktestSimulator depends on FillPolicy and strategy callbacks.
- StrategyRunner depends on TradingKernel and RiskEngine; isolates per-strategy risk while managing global risk pause.
- **New**: Gap closure tests depend on comprehensive mock broker infrastructure and rate limiting validation.

```mermaid
graph TB
Kernel["TradingKernel"] --> MarketEng["MarketEngine"]
Kernel --> CandleEng["CandleEngine"]
Kernel --> IndicatorEng["IndicatorEngine"]
Kernel --> RiskEng["RiskEngine"]
Kernel --> OrderEng["OrderEngine"]
Kernel --> Portfolio["Portfolio"]
Kernel --> Broker["BrokerAdapter"]
Replay["ReplayEngine"] --> Store["EventStore"]
Replay --> Clock["ReplayClock"]
Backtest["BacktestSimulator"] --> Fill["FillPolicy"]
Runner["StrategyRunner"] --> Kernel
Runner --> RiskEng
GapTests["Gap Closure Tests"] --> Broker
GapTests --> RateLimit["Rate Limit Validation"]
```

**Diagram sources**
- [test_engine_pipeline.py:49-57](file://tests/test_engine_pipeline.py#L49-57)
- [test_replay_backtest.py:72-99](file://tests/test_replay_backtest.py#L72-99)
- [test_strategy_runner.py:72-107](file://tests/test_strategy_runner.py#L72-107)
- [test_gap_closure.py:22-47](file://tests/test_gap_closure.py#L22-47)

**Section sources**
- [test_engine_pipeline.py:49-57](file://tests/test_engine_pipeline.py#L49-57)
- [test_replay_backtest.py:72-99](file://tests/test_replay_backtest.py#L72-99)
- [test_strategy_runner.py:72-107](file://tests/test_strategy_runner.py#L72-107)
- [test_gap_closure.py:22-47](file://tests/test_gap_closure.py#L22-47)

## Performance Considerations
- Use measure_tick_throughput to quantify ticks/sec and wall time; integrate into CI to detect regressions.
- Increase n_ticks for load testing; monitor memory usage of IndicatorEngine via max_rows.
- Prefer ReplayClock and deterministic feeds to avoid flaky timing-dependent tests.
- **New**: Gap closure tests ensure rate limiting doesn't impact performance by validating proper exception handling.

## Troubleshooting Guide
Common issues and resolutions:
- Stale orders not evicted: ensure BrokerExecution.poll() runs enough cycles to trigger eviction on repeated failures.
- Partial fills double-counted: verify poll() emits delta quantities and tracks open orders correctly.
- Unknown event types in JSONL: EventStore silently skips unknown __type__ entries; ensure consistent serialization.
- Non-numeric avg_price from broker: fallback to intent price; robust parsing prevents poll loop crashes.
- Position sync transient failures: ensure sync_positions() preserves local state and does not zero balance on errors.
- **New**: Rate limited exceptions being swallowed: verify executed_price() methods properly propagate RateLimited exceptions instead of returning 0.0.

**Section sources**
- [test_broker_executor.py:13-39](file://tests/test_broker_executor.py#L13-39)
- [test_live_execution.py:262-377](file://tests/test_live_execution.py#L262-377)
- [test_replay_backtest.py:243-267](file://tests/test_replay_backtest.py#L243-267)
- [test_gap_closure.py:454-521](file://tests/test_gap_closure.py#L454-521)

## Conclusion
The nTrade test suite demonstrates robust methodologies for validating trading pipelines under both simulated and live conditions. By leveraging synthetic feeds, replay engines, and mocked brokers, tests achieve deterministic, repeatable outcomes. The inclusion of risk breakers, strategy isolation, performance benchmarks, and comprehensive gap closure testing with proper rate limiting exception propagation ensures reliability, safety, and scalability. Adopt these patterns to build resilient strategies and maintain high-quality integrations.

## Appendices
- Best practices for test organization: group by feature, use fixtures for common kernels and clocks, isolate external dependencies via mocks/stubs.
- Continuous integration setup: run pytest suites, capture benchmark outputs, fail on throughput regressions, and archive artifacts.
- **New**: Gap closure testing patterns for ensuring proper error propagation and avoiding silent failures in production environments.