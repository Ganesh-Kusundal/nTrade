# Basic Usage Examples

<cite>
**Referenced Files in This Document**
- [ema_cross_run.py](file://scripts/ema_cross_run.py)
- [paper_gate_run.py](file://scripts/paper_gate_run.py)
- [live_read_check.py](file://scripts/live_read_check.py)
- [trading_session.py](file://ntrade/kernel/trading_session.py)
- [session.py](file://ntrade/kernel/session.py)
- [clock.py](file://ntrade/kernel/clock.py)
- [market_feed.py](file://ntrade/sources/market_feed.py)
- [synthetic_feed.py](file://ntrade/sources/synthetic_feed.py)
- [strategies.py](file://ntrade/engines/strategies.py)
- [simulator.py](file://ntrade/execution/simulator.py)
- [portfolio.py](file://ntrade/domain/portfolio.py)
- [cash.py](file://ntrade/domain/instruments/cash.py)
- [facade.py](file://ntrade/facade.py)
- [paper.py](file://ntrade/brokers/paper.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This guide provides practical, beginner-friendly examples to help you get started with nTrade. You will learn how to:
- Connect to a broker via TradingSession
- Create instruments and access market data
- Run historical backtests using replay mode
- Set up real-time streaming with simulated feeds
- Place orders through the kernel’s execution pipeline
- Manage portfolios and track positions and balances
- Configure common patterns, handle errors, and debug your code

The examples are grounded in the repository’s working scripts and core modules, ensuring that what you learn here maps directly to production-grade behavior.

## Project Structure
At a high level, nTrade is organized into layers:
- Brokers: adapters for live brokers (e.g., Dhan) and paper trading
- Domain: instruments, market data models, portfolio/account
- Engines: event-driven processing (candles, indicators, strategies, risk, portfolio)
- Kernel: orchestrates engines, clock, event bus, and execution routing
- Sources: feed canonical events (ticks, quotes) into the kernel
- Execution: simulated or broker-backed order filling
- Scripts: runnable examples demonstrating end-to-end flows

```mermaid
graph TB
subgraph "Scripts"
A["ema_cross_run.py"]
B["paper_gate_run.py"]
C["live_read_check.py"]
end
subgraph "Kernel"
K1["TradingSession"]
K2["TradingKernel"]
K3["Clocks"]
end
subgraph "Sources"
S1["SimulatedFeedSource"]
S2["SyntheticMarketFeedSource"]
end
subgraph "Engines"
E1["CandleEngine"]
E2["IndicatorEngine"]
E3["StrategyEngine"]
E4["RiskEngine"]
E5["OrderEngine"]
E6["PortfolioEngine"]
end
subgraph "Execution"
X1["SimulatedExecution"]
X2["BrokerExecution"]
end
subgraph "Domain"
D1["Instruments (Index, Equity, ...)"]
D2["Portfolio & Account"]
end
A --> K1
B --> K1
C --> K1
K1 --> K2
K2 --> E1
K2 --> E2
K2 --> E3
K2 --> E4
K2 --> E5
K2 --> E6
S1 --> K2
S2 --> K2
E5 --> X1
E5 --> X2
K2 --> D1
K2 --> D2
```

**Diagram sources**
- [ema_cross_run.py:1-87](file://scripts/ema_cross_run.py#L1-L87)
- [paper_gate_run.py:1-66](file://scripts/paper_gate_run.py#L1-L66)
- [live_read_check.py:1-188](file://scripts/live_read_check.py#L1-L188)
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [clock.py:1-55](file://ntrade/kernel/clock.py#L1-L55)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)
- [strategies.py:1-67](file://ntrade/engines/strategies.py#L1-L67)
- [simulator.py:1-147](file://ntrade/execution/simulator.py#L1-L147)
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)
- [cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)

**Section sources**
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)

## Core Components
- TradingSession: Unified entry point to connect to brokers, create instruments, register strategies, and start/stop sessions. Supports live, paper, and replay modes.
- TradingKernel: Wires the engine stack (market, candles, indicators, strategy, risk, order, portfolio), manages the event bus, clock, and execution target.
- Instruments: Domain objects representing assets like Index, Equity, ETF, Currency, Commodity.
- Market Data Sources: SimulatedFeedSource and SyntheticMarketFeedSource produce canonical TickEvent/QuoteEvent streams for offline testing.
- Strategies: Reusable logic like EmaCrossStrategy reacting to candle events and emitting signals.
- Execution: SimulatedExecution for deterministic fills; BrokerExecution for live broker integration.
- Portfolio & Account: Track positions, holdings, balance, and P&L.

Key usage patterns:
- Connect via TradingSession.connect("dhan") or TradingSession.paper()
- Create instruments via session.index(), session.stock(), etc.
- Register instruments and strategies with the kernel
- Start the kernel and feed events from a source
- Read account/portfolio state after run

**Section sources**
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)
- [strategies.py:1-67](file://ntrade/engines/strategies.py#L1-L67)
- [simulator.py:1-147](file://ntrade/execution/simulator.py#L1-L147)
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)

## Architecture Overview
The zero-parity design ensures identical behavior across live, replay, and backtest by funneling all market data through canonical events and a shared kernel.

```mermaid
sequenceDiagram
participant User as "User Script"
participant Session as "TradingSession"
participant Kernel as "TradingKernel"
participant Source as "SimulatedFeedSource"
participant Bus as "EventBus"
participant Candle as "CandleEngine"
participant Ind as "IndicatorEngine"
participant Strat as "StrategyEngine"
participant Risk as "RiskEngine"
participant Order as "OrderEngine"
participant Exec as "SimulatedExecution"
participant Port as "PortfolioEngine"
User->>Session : connect("dhan") / paper()
User->>Session : index(symbol)
User->>Kernel : register(instrument)
User->>Kernel : register_strategy(EmaCrossStrategy)
User->>Kernel : start()
User->>Source : start(data=historical frame)
Source->>Bus : publish QuoteEvent/TickEvent
Bus->>Candle : update candles
Candle-->>Ind : emit CandleClosedEvent
Ind-->>Strat : indicator bundle updated
Strat-->>Risk : emit SignalGeneratedEvent
Risk-->>Order : emit OrderIntentEvent
Order-->>Exec : submit intent
Exec-->>Port : publish fill events
Port-->>User : position/balance updates
User->>Kernel : stop(reason="end of data")
```

**Diagram sources**
- [ema_cross_run.py:1-87](file://scripts/ema_cross_run.py#L1-L87)
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [strategies.py:1-67](file://ntrade/engines/strategies.py#L1-L67)
- [simulator.py:1-147](file://ntrade/execution/simulator.py#L1-L147)

## Detailed Component Analysis

### EMA Crossover Backtest (Historical Data + Replay)
This example demonstrates fetching historical data, setting up a replay kernel, registering a strategy, feeding events, and reporting results.

Steps:
- Connect to a broker session and fetch historical OHLCV for an index
- Initialize a TradingKernel in replay mode with a ReplayClock
- Register the instrument and EmaCrossStrategy
- Feed historical data via SimulatedFeedSource
- Stop the kernel and print event counts, fills, final position, balance, and equity

```mermaid
flowchart TD
Start(["Start"]) --> Fetch["Fetch historical OHLCV for symbol"]
Fetch --> KernelInit["Create TradingKernel(mode='replay', timeframe='5m')"]
KernelInit --> RegisterInst["Register Index(symbol)"]
RegisterInst --> RegisterStrat["Register EmaCrossStrategy(fast=9, slow=21, quantity=5)"]
RegisterStrat --> StartKernel["Kernel.start()"]
StartKernel --> Feed["SimulatedFeedSource.start(data=frame)"]
Feed --> Flush["Flush final candle"]
Flush --> StopKernel["Kernel.stop(reason='end of historical data')"]
StopKernel --> Report["Print events, fills, position, balance, equity"]
Report --> End(["End"])
```

**Diagram sources**
- [ema_cross_run.py:1-87](file://scripts/ema_cross_run.py#L1-L87)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [strategies.py:1-67](file://ntrade/engines/strategies.py#L1-L67)
- [clock.py:1-55](file://ntrade/kernel/clock.py#L1-L55)

**Section sources**
- [ema_cross_run.py:1-87](file://scripts/ema_cross_run.py#L1-L87)
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [strategies.py:1-67](file://ntrade/engines/strategies.py#L1-L67)
- [clock.py:1-55](file://ntrade/kernel/clock.py#L1-L55)

### Paper Trading Gate (Validation Checklist)
Use this script to validate a strategy over synthetic ticks derived from historical bars before going live. It prints a checklist and exits non-zero if conditions fail (e.g., no fills or excessive drawdown).

Steps:
- Connect to a broker session and fetch historical data
- Initialize a replay kernel and register instrument + strategy
- Use SyntheticMarketFeedSource to generate 1-second ticks per minute bar
- Join the feed thread, flush candles, stop kernel, and build a report
- Fail closed based on fills and max drawdown thresholds

```mermaid
sequenceDiagram
participant User as "User Script"
participant Session as "TradingSession"
participant Kernel as "TradingKernel"
participant Source as "SyntheticMarketFeedSource"
participant Report as "build_paper_report"
User->>Session : connect("dhan")
User->>Session : index(symbol)
User->>Kernel : register(Index)
User->>Kernel : register_strategy(EmaCrossStrategy)
User->>Kernel : start()
User->>Source : start(data=OHLCV frame)
Source-->>Kernel : publish QuoteEvent + TickEvents
User->>Source : join(timeout=120)
User->>Kernel : flush + stop(reason="paper gate complete")
User->>Report : build_paper_report(kernel, initial_cash)
Report-->>User : JSON report with fills, drawdown
alt Fails checks
User->>User : exit(1)
else Passes
User->>User : exit(0)
end
```

**Diagram sources**
- [paper_gate_run.py:1-66](file://scripts/paper_gate_run.py#L1-L66)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)

**Section sources**
- [paper_gate_run.py:1-66](file://scripts/paper_gate_run.py#L1-L66)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)

### Live Read-Only Check (Connectivity and API Endpoints)
Verify connection and read-only endpoints against a live broker without placing orders. Useful for environment setup and troubleshooting.

Steps:
- Connect to the broker via TradingSession.connect("dhan")
- Validate quote, depth, history, option chain, expiry lists, and account endpoints
- Record PASS/FAIL/DEGRADED statuses and summarize results

```mermaid
flowchart TD
Start(["Start"]) --> Connect["Connect to broker via TradingSession"]
Connect --> Quotes["Validate quote.ltp and full quote data"]
Quotes --> Depth["Validate depth and depth20 capabilities"]
Depth --> History["Validate history for multiple timeframes"]
History --> Options["Validate option chain and Greeks"]
Options --> Account["Validate balance, positions, orderbook, tradebook"]
Account --> Report["Summarize results and exit status"]
Report --> End(["End"])
```

**Diagram sources**
- [live_read_check.py:1-188](file://scripts/live_read_check.py#L1-L188)
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)

**Section sources**
- [live_read_check.py:1-188](file://scripts/live_read_check.py#L1-L188)
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)

### Real-Time Streaming Setup (Simulated)
For local development and testing, use SimulatedFeedSource to stream canonical events from a price path or OHLCV frame. This mirrors live websockets so strategies behave identically.

Steps:
- Create a TradingKernel (live or replay)
- Attach SimulatedFeedSource with either prices list or OHLCV DataFrame
- Start the source to publish TickEvent/QuoteEvent
- Observe strategy reactions and portfolio updates

```mermaid
classDiagram
class MarketFeedSource {
+start() void
+stop() void
+attach(kernel) MarketFeedSource
+bus EventBus
}
class SimulatedFeedSource {
+symbol string
+exchange string
+prices list[float]
+data DataFrame
+start() void
-_feed_prices() void
-_feed_frame() void
}
MarketFeedSource <|-- SimulatedFeedSource
```

**Diagram sources**
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)

**Section sources**
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)

### Historical Data Retrieval (via Instrument.history)
Access historical OHLCV data through the instrument object created by TradingSession. The returned series includes a DataFrame for downstream processing.

Steps:
- Use session.index(symbol) to create an Index instrument
- Call instrument.history(timeframe, days=N, force=True) to fetch data
- Access series.df for pandas operations

```mermaid
flowchart TD
Start(["Start"]) --> CreateInstrument["session.index(symbol)"]
CreateInstrument --> FetchHistory["instrument.history(timeframe, days, force=True)"]
FetchHistory --> DataFrame["series.df for analysis"]
DataFrame --> End(["End"])
```

**Diagram sources**
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)

**Section sources**
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)

### Basic Order Placement (Through Strategy Signals)
Orders are placed via the kernel’s execution pipeline when strategies emit signals. In simulation, SimulatedExecution generates deterministic fills with configurable costs and slippage.

Steps:
- Implement or register a Strategy that emits signals on candle closures
- The StrategyEngine routes signals to RiskEngine and then OrderEngine
- OrderEngine submits intents to SimulatedExecution (or BrokerExecution live)
- Fills update PortfolioEngine and account/position state

```mermaid
sequenceDiagram
participant Strat as "EmaCrossStrategy"
participant SE as "StrategyEngine"
participant RE as "RiskEngine"
participant OE as "OrderEngine"
participant Exec as "SimulatedExecution"
participant PE as "PortfolioEngine"
Strat-->>SE : on_candle_closed -> emit_signal(...)
SE-->>RE : SignalGeneratedEvent
RE-->>OE : SignalApprovedEvent -> OrderIntentEvent
OE-->>Exec : submit(intent)
Exec-->>PE : OrderFilledEvent
PE-->>Strat : PositionUpdated/BalanceChanged events
```

**Diagram sources**
- [strategies.py:1-67](file://ntrade/engines/strategies.py#L1-L67)
- [simulator.py:1-147](file://ntrade/execution/simulator.py#L1-L147)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)

**Section sources**
- [strategies.py:1-67](file://ntrade/engines/strategies.py#L1-L67)
- [simulator.py:1-147](file://ntrade/execution/simulator.py#L1-L147)
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)

### Portfolio Management (Positions and Balance)
Track positions and balance updates through the domain models and kernel context.

Key concepts:
- Position tracks symbol, quantity, avg_price, ltp, product, exchange
- Portfolio aggregates positions and computes pnl and market value
- Account holds balance and holdings, supports refresh from broker

```mermaid
classDiagram
class Position {
+string symbol
+int quantity
+float avg_price
+float ltp
+string product
+string exchange
+market_value float
+pnl float
}
class Portfolio {
+positions list[Position]
+holdings list[Holding]
+pnl float
+market_value float
+position(symbol) Position
+refresh() Portfolio
}
class Account {
+float balance
+holdings list[Holding]
+holding(symbol) Holding
+refresh() Account
}
Portfolio --> Position : "contains"
Account --> Holding : "contains"
```

**Diagram sources**
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)

**Section sources**
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)

### Connecting to Different Brokers and Paper Trading
- Live broker: TradingSession.connect("dhan") retrieves a broker adapter from the registry and initializes a live session
- Paper trading: TradingSession.paper() seeds a PaperBroker with initial cash for deterministic runs
- Legacy facade: Market(broker="dhan") wraps TradingSession for backward compatibility

```mermaid
flowchart TD
Start(["Start"]) --> ChooseMode{"Choose mode"}
ChooseMode --> |Live| ConnectDhan["TradingSession.connect('dhan')"]
ChooseMode --> |Paper| PaperSession["TradingSession.paper(initial_cash=...)"]
ChooseMode --> |Legacy| LegacyFacade["Market(broker='dhan')"]
ConnectDhan --> Ready["Connected session ready"]
PaperSession --> Ready
LegacyFacade --> Ready
Ready --> End(["End"])
```

**Diagram sources**
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [facade.py:1-101](file://ntrade/facade.py#L1-L101)

**Section sources**
- [trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [facade.py:1-101](file://ntrade/facade.py#L1-L101)

### Running Simple Backtests
Backtesting uses the same kernel stack with a ReplayClock and deterministic execution. You can replay recorded events or synthesize ticks from OHLCV frames.

Steps:
- Initialize TradingKernel(mode="replay", clock=ReplayClock())
- Register instruments and strategies
- Feed events via SimulatedFeedSource or SyntheticMarketFeedSource
- Analyze fills and portfolio state after stop

```mermaid
flowchart TD
Start(["Start"]) --> InitKernel["TradingKernel(mode='replay', clock=ReplayClock())"]
InitKernel --> Register["Register instruments and strategies"]
Register --> Feed["Feed events (Simulated/Synthetic)"]
Feed --> Stop["Kernel.stop(reason='backtest complete')"]
Stop --> Analyze["Analyze fills and portfolio state"]
Analyze --> End(["End"])
```

**Diagram sources**
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [clock.py:1-55](file://ntrade/kernel/clock.py#L1-L55)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)

**Section sources**
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [clock.py:1-55](file://ntrade/kernel/clock.py#L1-L55)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)

## Dependency Analysis
The kernel wires engines and execution targets in a cohesive pipeline. Modes differ only by clock and execution target, preserving zero parity.

```mermaid
graph TB
K["TradingKernel"]
ME["MarketEngine"]
CE["CandleEngine"]
IE["IndicatorEngine"]
SE["StrategyEngine"]
RE["RiskEngine"]
OE["OrderEngine"]
PE["PortfolioEngine"]
ER["ExecutionRouter"]
BE["BrokerExecution"]
SIM["SimulatedExecution"]
K --> ME
K --> CE
K --> IE
K --> SE
K --> RE
K --> OE
K --> PE
OE --> ER
ER --> BE
ER --> SIM
```

**Diagram sources**
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)

**Section sources**
- [session.py:1-198](file://ntrade/kernel/session.py#L1-L198)

## Performance Considerations
- Prefer vectorized pandas operations for historical data processing
- Use ReplayClock for deterministic replay to avoid wall-clock jitter
- Minimize event churn by batching where possible (e.g., per-bar QuoteEvent plus synthesized ticks)
- Avoid unnecessary refresh calls; cache instrument metadata once hydrated
- For live runs, poll orders and sync positions at reasonable intervals to reduce overhead

## Troubleshooting Guide
Common issues and debugging techniques:
- Connection failures: Use live_read_check.py to validate endpoints and credentials
- No fills in backtests: Ensure instruments are registered and indicators have enough warm-up candles
- Degraded responses: Some endpoints may return partial data; check sanity predicates in live_read_check.py
- Error logging for orders: Capture stdout around order placement and log both request and raw reply; many rejections provide no explicit reason
- Event inspection: Print kernel.bus.history to see event flow and counts

Practical tips:
- Wrap order placement blocks in try/except and log sent/received details
- Use paper mode to isolate strategy logic from network variability
- Flush candles before stopping kernels to ensure final bars are processed

**Section sources**
- [live_read_check.py:1-188](file://scripts/live_read_check.py#L1-L188)
- [ema_cross_run.py:1-87](file://scripts/ema_cross_run.py#L1-L87)
- [paper_gate_run.py:1-66](file://scripts/paper_gate_run.py#L1-L66)

## Conclusion
You now have a solid foundation to build and test trading systems with nTrade:
- Use TradingSession for unified broker connections and instrument creation
- Leverage TradingKernel and engines for robust, zero-parity event processing
- Employ SimulatedFeedSource and SyntheticMarketFeedSource for realistic offline testing
- Track portfolio and account state to validate performance and risk
- Follow the provided scripts as templates for live validation and backtesting

## Appendices
- Configuration patterns:
  - Initial cash seeding via TradingSession.paper(initial_cash=...)
  - Timeframe selection for candle aggregation
  - Statutory cost configuration in SimulatedExecution for realistic P&L
- Debugging utilities:
  - Event history inspection via kernel.bus.history
  - Paper gate reports for go-live readiness
  - Live read-only checks for environment health