# Backtesting & Simulation

<cite>
**Referenced Files in This Document**
- [simulator.py](file://ntrade/backtest/simulator.py)
- [fills.py](file://ntrade/backtest/fills.py)
- [tick_simulator.py](file://ntrade/sim/tick_simulator.py)
- [replay_engine.py](file://ntrade/replay/replay_engine.py)
- [event_store.py](file://ntrade/storage/event_store.py)
- [synthetic_feed.py](file://ntrade/sources/synthetic_feed.py)
- [simulator.py](file://ntrade/execution/simulator.py)
- [costs.py](file://ntrade/execution/costs.py)
- [clock.py](file://ntrade/kernel/clock.py)
- [session.py](file://ntrade/kernel/session.py)
- [test_tick_simulator.py](file://tests/test_tick_simulator.py)
- [test_synthetic_feed.py](file://tests/test_synthetic_feed.py)
- [test_kernel_recording.py](file://tests/test_kernel_recording.py)
- [test_replay_backtest.py](file://tests/test_replay_backtest.py)
</cite>

## Table of Contents
1. Introduction
2. Project Structure
3. Core Components
4. Architecture Overview
5. Detailed Component Analysis
6. Dependency Analysis
7. Performance Considerations
8. Troubleshooting Guide
9. Conclusion

## Introduction
This document explains the backtesting and simulation capabilities that provide zero-parity behavior across live, replay, and backtest modes. It covers:
- BacktestSimulator for running strategies over historical OHLCV with deterministic fills and cost modeling
- BarAwareExecution for realistic limit order filling based on bar price action
- Tick simulator for generating deterministic 1-second ticks from 1-minute OHLCV bars
- ReplayEngine for deterministic replay of recorded sessions with exact timing and event ordering
- EventStore for append-only event persistence enabling audit trails and crash recovery
- Synthetic feed system for offline testing without live market connections
- Configuration of fill models, recording, performance optimization, and result analysis techniques

## Project Structure
The backtesting and simulation features are implemented across several modules:
- Backtest orchestration and results: ntrade/backtest
- Deterministic tick synthesis: ntrade/sim
- Replay engine: ntrade/replay
- Append-only event store: ntrade/storage
- Synthetic feed source: ntrade/sources
- Simulated execution and costs: ntrade/execution
- Kernel and clock abstractions: ntrade/kernel

```mermaid
graph TB
subgraph "Backtest"
BS["BacktestSimulator"]
BA["BarAwareExecution"]
end
subgraph "Simulation"
TS["Tick Simulator"]
SF["SyntheticFeedSource"]
end
subgraph "Replay"
RE["ReplayEngine"]
ES["EventStore"]
end
subgraph "Execution"
SE["SimulatedExecution"]
COSTS["Cost Models"]
end
subgraph "Kernel"
CLK["TradingClock"]
TK["TradingKernel"]
end
BS --> TK
BS --> BA
BA --> SE
SF --> TS
RE --> TK
TK --> CLK
SE --> COSTS
TK --> ES
```

**Diagram sources**
- [simulator.py:58-113](file://ntrade/backtest/simulator.py#L58-L113)
- [fills.py:44-72](file://ntrade/backtest/fills.py#L44-L72)
- [tick_simulator.py:51-82](file://ntrade/sim/tick_simulator.py#L51-L82)
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [replay_engine.py:14-24](file://ntrade/replay/replay_engine.py#L14-L24)
- [event_store.py:76-100](file://ntrade/storage/event_store.py#L76-L100)
- [simulator.py:43-147](file://ntrade/execution/simulator.py#L43-L147)
- [costs.py:70-200](file://ntrade/execution/costs.py#L70-L200)
- [clock.py:14-55](file://ntrade/kernel/clock.py#L14-L55)
- [session.py:38-101](file://ntrade/kernel/session.py#L38-L101)

**Section sources**
- [simulator.py:58-113](file://ntrade/backtest/simulator.py#L58-L113)
- [fills.py:16-72](file://ntrade/backtest/fills.py#L16-L72)
- [tick_simulator.py:20-82](file://ntrade/sim/tick_simulator.py#L20-L82)
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [replay_engine.py:14-24](file://ntrade/replay/replay_engine.py#L14-L24)
- [event_store.py:76-100](file://ntrade/storage/event_store.py#L76-L100)
- [simulator.py:43-147](file://ntrade/execution/simulator.py#L43-L147)
- [costs.py:70-200](file://ntrade/execution/costs.py#L70-L200)
- [clock.py:14-55](file://ntrade/kernel/clock.py#L14-L55)
- [session.py:38-101](file://ntrade/kernel/session.py#L38-L101)

## Core Components
- BacktestSimulator: Drives a TradingKernel over an OHLCV DataFrame, publishing QuoteEvent and TickEvent per bar, applying futures carry/roll costs, and producing BacktestResult with equity curve and trade details.
- BarAwareExecution: Extends SimulatedExecution to enforce bar-aware limit fills using FillPolicy; MARKET orders behave identically to parent.
- Tick Simulator: Generates deterministic 1-second ticks from 1-minute OHLCV respecting open/close anchors, high/low bounds, and volume distribution.
- ReplayEngine: Replays an event stream through a TradingKernel with ReplayClock, ensuring identical decisions as live trading.
- EventStore: Append-only persistence of events to JSONL or memory; supports querying, reconstruction of open-order deltas, and causal recovery events.
- SyntheticMarketFeedSource: Converts 1m OHLCV into deterministic 1-second ticks and publishes QuoteEvent per bar; integrates seamlessly with kernel.
- SimulatedExecution: Deterministic fill pipeline with configurable slippage, commission, statutory charges, and delivery detection.
- Cost Models: SlippageModel, CommissionModel, IndianStatutoryCosts for accurate PnL convergence with live markets.
- Clock Abstraction: LiveClock, ReplayClock, SimulationClock ensure time determinism across modes.

**Section sources**
- [simulator.py:58-113](file://ntrade/backtest/simulator.py#L58-L113)
- [fills.py:16-72](file://ntrade/backtest/fills.py#L16-L72)
- [tick_simulator.py:51-82](file://ntrade/sim/tick_simulator.py#L51-L82)
- [replay_engine.py:14-24](file://ntrade/replay/replay_engine.py#L14-L24)
- [event_store.py:76-100](file://ntrade/storage/event_store.py#L76-L100)
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [simulator.py:43-147](file://ntrade/execution/simulator.py#L43-L147)
- [costs.py:70-200](file://ntrade/execution/costs.py#L70-L200)
- [clock.py:14-55](file://ntrade/kernel/clock.py#L14-L55)

## Architecture Overview
Zero-parity is achieved by keeping the same TradingKernel + engine stack + execution target across live, replay, and backtest. Only the event source, clock, and execution target differ.

```mermaid
sequenceDiagram
participant Data as "OHLCV Data"
participant BS as "BacktestSimulator"
participant CLK as "SimulationClock"
participant K as "TradingKernel"
participant BUS as "EventBus"
participant EX as "ExecutionTarget"
participant COST as "CostModels"
Data->>BS : Iterate rows (timestamp/open/high/low/close/volume)
BS->>CLK : set(ts)
BS->>BUS : publish QuoteEvent(bar)
BS->>BUS : publish TickEvent(close, volume)
K->>K : engines process events (market/candle/indicator/strategy)
K->>EX : submit OrderIntentEvent
EX->>COST : compute slippage/commission/statutory
EX-->>K : publish OrderAcceptedEvent / OrderFilledEvent
BS->>BS : apply futures costs, mark-to-market
BS-->>Data : collect equity curve and trades
```

**Diagram sources**
- [simulator.py:116-143](file://ntrade/backtest/simulator.py#L116-L143)
- [session.py:133-145](file://ntrade/kernel/session.py#L133-L145)
- [simulator.py:72-147](file://ntrade/execution/simulator.py#L72-L147)
- [costs.py:164-174](file://ntrade/execution/costs.py#L164-L174)

## Detailed Component Analysis

### BacktestSimulator
- Initializes a TradingKernel in backtest mode with SimulationClock and optional custom instrument.
- Registers an ExecutionRouter with either BarAwareExecution (when fill_policy provided) or SimulatedExecution.
- For each bar, publishes QuoteEvent and TickEvent, applies futures carry/roll costs, and records equity curve.
- Results aggregate fills, commissions, statutory costs, futures costs, max drawdown, and total return.

```mermaid
classDiagram
class BacktestSimulator {
+string symbol
+string exchange
+string timeframe
+float initial_cash
+SlippageModel slippage
+CommissionModel commission
+FuturesCarryCosts futures_costs
+FillPolicy fill_policy
+SimulationClock clock
+run(data) BacktestResult
+register_strategy(strategy) BacktestSimulator
-_apply_futures_costs(ts) void
-_deduct_futures(amount) void
-_mark_to_market(close) float
+results() BacktestResult
}
class BacktestResult {
+float final_equity
+float total_return_pct
+list trades
+DataFrame equity_curve
+int n_trades
+float commissions_total
+float statutory_total
+float futures_costs_total
+float max_drawdown_pct
+costs_total() float
}
BacktestSimulator --> BacktestResult : "produces"
```

**Diagram sources**
- [simulator.py:29-56](file://ntrade/backtest/simulator.py#L29-L56)
- [simulator.py:58-113](file://ntrade/backtest/simulator.py#L58-L113)
- [simulator.py:194-219](file://ntrade/backtest/simulator.py#L194-L219)

**Section sources**
- [simulator.py:58-113](file://ntrade/backtest/simulator.py#L58-L113)
- [simulator.py:116-143](file://ntrade/backtest/simulator.py#L116-L143)
- [simulator.py:145-192](file://ntrade/backtest/simulator.py#L145-L192)
- [simulator.py:194-219](file://ntrade/backtest/simulator.py#L194-L219)

### BarAwareExecution and FillPolicy
- FillPolicy defines market_on behavior and limit_fill logic against bar OHLC.
- BarAwareExecution wraps SimulatedExecution to reject limit orders not touched by the bar and adjust fill price accordingly.

```mermaid
flowchart TD
Start(["submit(intent)"]) --> CheckType{"order_type == MARKET?"}
CheckType --> |Yes| ParentSubmit["Call SimulatedExecution.submit(intent)"]
CheckType --> |No| GetBar["bar = bar_provider()"]
GetBar --> HasBar{"bar is not None?"}
HasBar --> |No| ParentSubmit
HasBar --> |Yes| LimitPrice["price = policy.limit_fill(intent, bar)"]
LimitPrice --> Touched{"price is not None?"}
Touched --> |No| Reject["Return OrderRejectedEvent(limit not touched)"]
Touched --> |Yes| AdjustIntent["intent.price = price"]
AdjustIntent --> ParentSubmit
ParentSubmit --> End(["Return result"])
```

**Diagram sources**
- [fills.py:16-42](file://ntrade/backtest/fills.py#L16-L42)
- [fills.py:44-72](file://ntrade/backtest/fills.py#L44-L72)

**Section sources**
- [fills.py:16-42](file://ntrade/backtest/fills.py#L16-L42)
- [fills.py:44-72](file://ntrade/backtest/fills.py#L44-L72)

### Tick Simulator
- Generates deterministic 1-second ticks from 1-minute OHLCV with invariants:
  - One tick per second
  - First tick equals open, last equals close
  - All prices within [low, high]
  - Max/min equal high/low
  - Sum of quantities equals bar volume
  - Same seed yields identical ticks

```mermaid
flowchart TD
Start(["synthesize_1m_ticks"]) --> Validate["Validate high >= low and seconds >= 4"]
Validate --> RNG["Create Random(seed)"]
RNG --> Anchors["Pick random hi/lo indices and build anchors [(0,open),(hi,high),(lo,low),(n-1,close)]"]
Anchors --> Interp["Interpolate prices via _interp"]
Interp --> Noise["Add small noise bounded by [low,high]"]
Noise --> Round["Round prices to 2 decimals"]
Round --> PinAnchors["Pin anchors to open/high/low/close"]
PinAnchors --> Volume["Distribute volume proportionally via _distribute_volume"]
Volume --> BuildTicks["Build list of SimTick objects"]
BuildTicks --> End(["Return ticks"])
```

**Diagram sources**
- [tick_simulator.py:27-48](file://ntrade/sim/tick_simulator.py#L27-L48)
- [tick_simulator.py:51-82](file://ntrade/sim/tick_simulator.py#L51-L82)

**Section sources**
- [tick_simulator.py:51-82](file://ntrade/sim/tick_simulator.py#L51-L82)

### ReplayEngine
- Wraps a TradingKernel with ReplayClock to replay events deterministically.
- run(events, start=None) sets clock.start if provided and iterates events, setting clock to event.ts before publishing.

```mermaid
sequenceDiagram
participant Caller as "Caller"
participant RE as "ReplayEngine"
participant K as "TradingKernel"
participant CLK as "ReplayClock"
participant BUS as "EventBus"
Caller->>RE : run(events, start)
RE->>K : initialize with ReplayClock
alt start provided
RE->>CLK : set(start)
end
loop for each event
RE->>CLK : set(event.ts)
RE->>BUS : publish(event)
K->>K : engines process event
end
RE-->>Caller : return kernel
```

**Diagram sources**
- [replay_engine.py:14-24](file://ntrade/replay/replay_engine.py#L14-L24)
- [session.py:133-145](file://ntrade/kernel/session.py#L133-L145)
- [clock.py:31-47](file://ntrade/kernel/clock.py#L31-L47)

**Section sources**
- [replay_engine.py:14-24](file://ntrade/replay/replay_engine.py#L14-L24)
- [session.py:133-145](file://ntrade/kernel/session.py#L133-L145)

### EventStore
- Append-only storage of events with optional JSONL persistence.
- Supports querying by type/symbol, extracting market events, reconstructing open-order deltas, and providing causal recovery events sorted by timestamp and insertion order.

```mermaid
classDiagram
class EventStore {
+path Path
+append(event) EventStore
+extend(events) EventStore
+events(event_type, symbol) list[Event]
+market_events() list[Event]
+open_order_deltas() dict
+recovery_events() list[Event]
+replay() iterator
+clear() EventStore
+close() EventStore
-_load() void
-_ensure_fh() FileHandle
}
```

**Diagram sources**
- [event_store.py:76-100](file://ntrade/storage/event_store.py#L76-L100)
- [event_store.py:116-136](file://ntrade/storage/event_store.py#L116-L136)
- [event_store.py:137-181](file://ntrade/storage/event_store.py#L137-L181)
- [event_store.py:183-211](file://ntrade/storage/event_store.py#L183-L211)

**Section sources**
- [event_store.py:76-100](file://ntrade/storage/event_store.py#L76-L100)
- [event_store.py:116-136](file://ntrade/storage/event_store.py#L116-L136)
- [event_store.py:137-181](file://ntrade/storage/event_store.py#L137-L181)
- [event_store.py:183-211](file://ntrade/storage/event_store.py#L183-L211)

### SyntheticMarketFeedSource
- Converts 1m OHLCV into deterministic 1-second ticks using synthesize_1m_ticks.
- Publishes QuoteEvent per bar and TickEvent per simulated second; integrates with kernel clock.

```mermaid
sequenceDiagram
participant Src as "SyntheticMarketFeedSource"
participant K as "TradingKernel"
participant BUS as "EventBus"
participant TS as "Tick Simulator"
loop for each row in data
Src->>K : bus.publish(QuoteEvent(bar))
Src->>TS : synthesize_1m_ticks(bar)
TS-->>Src : list of SimTick
loop for each tick
Src->>K : clock.set(tick.ts)
Src->>BUS : publish(TickEvent(price, quantity, ts))
end
end
```

**Diagram sources**
- [synthetic_feed.py:52-77](file://ntrade/sources/synthetic_feed.py#L52-L77)
- [tick_simulator.py:51-82](file://ntrade/sim/tick_simulator.py#L51-L82)

**Section sources**
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)

### SimulatedExecution and Costs
- SimulatedExecution computes fill price, applies slippage/commission/statutory costs, handles delivery detection for equities, and publishes lifecycle events.
- Cost models include FixedSlippage, PercentageSlippage, FlatCommission, PercentageCommission, and IndianStatutoryCosts for comprehensive charge modeling.

```mermaid
flowchart TD
Submit(["submit(intent)"]) --> InstrumentCheck["instrument = ctx.instrument(symbol)"]
InstrumentCheck --> ValidInst{"instrument exists?"}
ValidInst --> |No| RejectUnknown["Return OrderRejectedEvent(unknown instrument)"]
ValidInst --> |Yes| PriceCalc{"order_type == MARKET?"}
PriceCalc --> |Yes| BasePrice["base = instrument._quote.ltp"]
BasePrice --> ValidBase{"base > 0?"}
ValidBase --> |No| RejectNoPrice["Return OrderRejectedEvent(no market price)"]
ValidBase --> |Yes| ApplySlip["fill_price = slippage.apply(base, side)"]
PriceCalc --> |No| UseLimit["fill_price = intent.price"]
ApplySlip --> ValidPrice{"fill_price > 0?"}
UseLimit --> ValidPrice
ValidPrice --> |No| RejectInvalid["Return OrderRejectedEvent(invalid fill price)"]
ValidPrice --> |Yes| DeliveryCheck["delivery_detection and side == SELL?"]
DeliveryCheck --> Statutory["statutory = IndianStatutoryCosts.for_instrument(...)"]
Statutory --> Publish["publish OrderAcceptedEvent and OrderFilledEvent"]
Publish --> End(["Return None"])
```

**Diagram sources**
- [simulator.py:72-147](file://ntrade/execution/simulator.py#L72-L147)
- [costs.py:164-174](file://ntrade/execution/costs.py#L164-L174)

**Section sources**
- [simulator.py:43-147](file://ntrade/execution/simulator.py#L43-L147)
- [costs.py:70-200](file://ntrade/execution/costs.py#L70-L200)

### Conceptual Overview
The zero-parity architecture ensures that the same strategy code runs identically across live, replay, and backtest modes by standardizing event types, clock behavior, and execution targets. Differences are limited to:
- Event source: live broker feed vs synthetic feed vs historical bars
- Clock: wall time vs deterministic replay/simulation clocks
- Execution target: real broker vs simulated execution with cost models

```mermaid
graph TB
Live["Live Mode"] --> BK["BrokerExecution"]
Replay["Replay Mode"] --> SE["SimulatedExecution"]
Backtest["Backtest Mode"] --> SE
SE --> COSTS["Cost Models"]
Live --> CLK_LIVE["LiveClock"]
Replay --> CLK_REPLAY["ReplayClock"]
Backtest --> CLK_SIM["SimulationClock"]
```

[No sources needed since this diagram shows conceptual workflow, not actual code structure]

## Dependency Analysis
Key dependencies and relationships:
- BacktestSimulator depends on TradingKernel, SimulationClock, ExecutionRouter, and either BarAwareExecution or SimulatedExecution.
- BarAwareExecution extends SimulatedExecution and uses FillPolicy and bar_provider.
- SyntheticMarketFeedSource depends on synthesize_1m_ticks and publishes QuoteEvent/TickEvent.
- ReplayEngine depends on TradingKernel and ReplayClock.
- EventStore persists all events and provides recovery utilities.
- SimulatedExecution relies on cost models and context for instrument state.

```mermaid
graph LR
BS["BacktestSimulator"] --> TK["TradingKernel"]
BS --> CLK["SimulationClock"]
BS --> ER["ExecutionRouter"]
ER --> BA["BarAwareExecution"]
ER --> SE["SimulatedExecution"]
BA --> POL["FillPolicy"]
SF["SyntheticMarketFeedSource"] --> TS["Tick Simulator"]
RE["ReplayEngine"] --> TK
TK --> ES["EventStore"]
SE --> COSTS["Cost Models"]
```

**Diagram sources**
- [simulator.py:58-113](file://ntrade/backtest/simulator.py#L58-L113)
- [fills.py:44-72](file://ntrade/backtest/fills.py#L44-L72)
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [replay_engine.py:14-24](file://ntrade/replay/replay_engine.py#L14-L24)
- [event_store.py:76-100](file://ntrade/storage/event_store.py#L76-L100)
- [simulator.py:43-147](file://ntrade/execution/simulator.py#L43-L147)

**Section sources**
- [simulator.py:58-113](file://ntrade/backtest/simulator.py#L58-L113)
- [fills.py:44-72](file://ntrade/backtest/fills.py#L44-L72)
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [replay_engine.py:14-24](file://ntrade/replay/replay_engine.py#L14-L24)
- [event_store.py:76-100](file://ntrade/storage/event_store.py#L76-L100)
- [simulator.py:43-147](file://ntrade/execution/simulator.py#L43-L147)

## Performance Considerations
- Large-scale backtests:
  - Use SimulationClock speed factor to accelerate time progression.
  - Prefer vectorized OHLCV processing where possible; avoid excessive Python loops inside strategies.
  - Disable unnecessary event recording when not needed for debugging.
- Memory management:
  - EventStore can grow large; use path-based JSONL persistence and clear/close after use.
  - Limit history retention in EventBus if long-running sessions.
- Result analysis:
  - BacktestResult includes equity curve, trade list, and aggregated costs; compute additional metrics (Sharpe, Sortino) externally.
  - Use market_events() and recovery_events() for focused analysis and debugging.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Invalid OHLCV input: Ensure non-empty DataFrame with required columns (timestamp, open, high, low, close, volume).
- Limit orders not filled: Verify bar touches limit price according to FillPolicy rules; check bar provider returns correct current bar.
- Determinism failures: Confirm consistent seed usage in tick simulator and avoid non-deterministic operations in strategies.
- Recording discrepancies: Use EventStore.market_events() for replay to avoid double-applying derived events; ensure causal ordering in recovery_events().

**Section sources**
- [simulator.py:116-120](file://ntrade/backtest/simulator.py#L116-L120)
- [fills.py:28-42](file://ntrade/backtest/fills.py#L28-L42)
- [tick_simulator.py:51-58](file://ntrade/sim/tick_simulator.py#L51-L58)
- [event_store.py:116-136](file://ntrade/storage/event_store.py#L116-L136)
- [event_store.py:183-211](file://ntrade/storage/event_store.py#L183-L211)

## Conclusion
The backtesting and simulation framework achieves zero-parity across live, replay, and backtest modes by standardizing event handling, clock abstraction, and execution targets. With deterministic tick generation, realistic fill policies, comprehensive cost modeling, and robust event persistence, it enables reliable strategy validation and performance analysis. Proper configuration of fill models, recording, and performance tuning ensures scalability and accuracy for large-scale simulations.

[No sources needed since this section summarizes without analyzing specific files]