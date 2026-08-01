# Architecture Overview

<cite>
**Referenced Files in This Document**
- [ARCHITECTURE.md](file://ARCHITECTURE.md)
- [ntrade/__init__.py](file://ntrade/__init__.py)
- [ntrade/facade.py](file://ntrade/facade.py)
- [ntrade/kernel/trading_session.py](file://ntrade/kernel/trading_session.py)
- [ntrade/kernel/session.py](file://ntrade/kernel/session.py)
- [ntrade/kernel/event_bus.py](file://ntrade/kernel/event_bus.py)
- [ntrade/kernel/resilient.py](file://ntrade/kernel/resilient.py)
- [ntrade/engines/market_engine.py](file://ntrade/engines/market_engine.py)
- [ntrade/engines/strategy_engine.py](file://ntrade/engines/strategy_engine.py)
- [ntrade/domain/instruments/base.py](file://ntrade/domain/instruments/base.py)
- [ntrade/domain/instruments/cash.py](file://ntrade/domain/instruments/cash.py)
- [ntrade/brokers/base.py](file://ntrade/brokers/base.py)
- [ntrade/brokers/capabilities.py](file://ntrade/brokers/capabilities.py)
- [ntrade/events/base.py](file://ntrade/events/base.py)
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
This document explains nTrade’s Clean Architecture design and its event-centric trading kernel that guarantees zero parity across live, replay, and backtest modes. It details the layered separation between public API, trading kernel, domain layer, broker layer, and infrastructure; describes component interactions including TradingKernel coordination, EventBus pub/sub, and engine stack processing; documents the domain model hierarchy from base Instrument through asset-specific implementations; and explains the broker abstraction pattern using BrokerAdapter and capability system. It also covers data flows from market data sources through engines to execution, cross-cutting concerns like error isolation and resilience, and technical decisions such as dependency injection, immutable data structures, and observer patterns.

## Project Structure
nTrade is organized into clear layers:
- Public API (facade + factories): Market facade and factory entry points for users.
- Trading Kernel (event-centric): TradingKernel, EventBus, clocks, engines, execution router, replay/backtest simulators.
- Domain Layer: Instruments, market data models, analytics, orders, session state.
- Broker Layer: Adapter abstraction with concrete brokers and capability extensions.
- Infrastructure: Transport, persistence (EventStore), replay, synthetic feeds.

```mermaid
graph TB
subgraph "Public API"
FAC["Market Facade"]
FCT["InstrumentFactory"]
end
subgraph "Trading Kernel"
KS["TradingKernel"]
EB["EventBus"]
CL["TradingClock"]
ENG["Engines (Market/Candle/Indicator/Strategy/Risk/Order/Portfolio)"]
RT["ExecutionRouter"]
RS["ResilientKernel"]
end
subgraph "Domain Layer"
INST["Instrument (base)"]
CASH["Equity/Index/ETF/Currency/Commodity/Bond/Crypto/Spot"]
MDEL["Quote/Tick/Depth/HistoricalSeries/LiveStream"]
end
subgraph "Broker Layer"
BA["BrokerAdapter (ABC)"]
CAP["Capability System"]
end
subgraph "Infrastructure"
ES["EventStore"]
RE["ReplayEngine"]
BS["BacktestSimulator"]
SF["SyntheticFeedSource"]
end
FAC --> FCT
FAC --> KS
KS --> EB
KS --> CL
KS --> ENG
KS --> RT
RS --> KS
INST --> CASH
INST --> MDEL
INST --> BA
BA --> CAP
KS --> ES
KS --> RE
KS --> BS
KS --> SF
```

**Diagram sources**
- [ntrade/facade.py:1-101](file://ntrade/facade.py#L1-L101)
- [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [ntrade/kernel/event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)
- [ntrade/kernel/resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)
- [ntrade/engines/market_engine.py:1-64](file://ntrade/engines/market_engine.py#L1-L64)
- [ntrade/domain/instruments/base.py:1-305](file://ntrade/domain/instruments/base.py#L1-L305)
- [ntrade/domain/instruments/cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)
- [ntrade/brokers/base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [ntrade/brokers/capabilities.py:1-74](file://ntrade/brokers/capabilities.py#L1-L74)

**Section sources**
- [ARCHITECTURE.md:20-51](file://ARCHITECTURE.md#L20-L51)
- [ntrade/__init__.py:1-105](file://ntrade/__init__.py#L1-L105)

## Core Components
- TradingSession: Unified entry point combining broker connection, instrument creation, kernel lifecycle, and strategy management.
- TradingKernel: Wires the engine stack, event bus, clock, and execution target; supports live/replay/backtest with identical stacks.
- EventBus: Synchronous pub/sub with MRO-based subscription, reentrant dispatch, and per-event history.
- Engines: Market, Candle, Indicator, Strategy, Risk, Order, Portfolio, PositionSync — each subscribes to canonical events and publishes derived events.
- Execution Router: Routes order intents to SimulatedExecution or BrokerExecution based on mode.
- ResilientKernel: Crash recovery by replaying causal events from EventStore without re-trading.

Key responsibilities and interactions are implemented in the following files:
- Session orchestration and lifecycle: [ntrade/kernel/trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- Kernel wiring and replay: [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- Pub/sub bus: [ntrade/kernel/event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)
- Engine pipeline (market projection): [ntrade/engines/market_engine.py:1-64](file://ntrade/engines/market_engine.py#L1-L64)
- Strategy hooks and signal emission: [ntrade/engines/strategy_engine.py:1-102](file://ntrade/engines/strategy_engine.py#L1-L102)
- Recovery flow: [ntrade/kernel/resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)

**Section sources**
- [ntrade/kernel/trading_session.py:1-306](file://ntrade/kernel/trading_session.py#L1-L306)
- [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [ntrade/kernel/event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)
- [ntrade/engines/market_engine.py:1-64](file://ntrade/engines/market_engine.py#L1-L64)
- [ntrade/engines/strategy_engine.py:1-102](file://ntrade/engines/strategy_engine.py#L1-L102)
- [ntrade/kernel/resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)

## Architecture Overview
The framework enforces a strict dependency rule: domain layer imports nothing from broker layer. Instruments receive a BrokerAdapter via constructor injection and access broker capabilities through a facade that resolves at call time. The kernel is event-centric: brokers, replays, and simulators are interchangeable event sources; instruments become read models updated by events rather than pulling state.

```mermaid
sequenceDiagram
participant Source as "Market Data Source"
participant Bus as "EventBus"
participant MK as "MarketEngine"
participant CD as "CandleEngine"
participant IN as "IndicatorEngine"
participant ST as "StrategyEngine"
participant RK as "RiskEngine"
participant OE as "OrderEngine"
participant EX as "ExecutionRouter"
participant BE as "BrokerExecution/SimulatedExecution"
participant PO as "PortfolioEngine"
Source->>Bus : TickEvent / QuoteEvent / DepthEvent
Bus-->>MK : Dispatch
MK->>MK : Update Instrument Read Model
MK->>Bus : QuoteUpdatedEvent
Bus-->>CD : CandleClosedEvent
Bus-->>IN : IndicatorUpdatedEvent
Bus-->>ST : SignalGeneratedEvent
ST->>RK : Approve/Reject
RK-->>OE : Approved Intent
OE->>EX : OrderIntentEvent
EX->>BE : Place Order
BE-->>PO : PositionUpdatedEvent / BalanceChangedEvent
```

**Diagram sources**
- [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [ntrade/engines/market_engine.py:1-64](file://ntrade/engines/market_engine.py#L1-L64)
- [ntrade/engines/strategy_engine.py:1-102](file://ntrade/engines/strategy_engine.py#L1-L102)

**Section sources**
- [ARCHITECTURE.md:20-51](file://ARCHITECTURE.md#L20-L51)
- [ARCHITECTURE.md:156-215](file://ARCHITECTURE.md#L156-L215)

## Detailed Component Analysis

### TradingKernel Coordination and Zero Parity
- TradingKernel wires engines and execution targets; only clock and execution target differ across modes.
- Replay uses ReplayClock to follow event timestamps; live uses LiveClock; backtest uses SimulationClock.
- EventStore records all published events; run_replay replays a timestamped stream deterministically.
- ResilientKernel recovers state by replaying causal events (market + fills) without re-trading, then rebuilds open-order deltas and reseeds execution sequences.

```mermaid
flowchart TD
Start([Start]) --> Mode{"Mode?"}
Mode --> |Live| LiveClock["Use LiveClock"]
Mode --> |Replay| ReplayClock["Use ReplayClock"]
Mode --> |Backtest| SimClock["Use SimulationClock"]
LiveClock --> Wire["Wire Engines + Router"]
ReplayClock --> Wire
SimClock --> Wire
Wire --> Record{"Record Events?"}
Record --> |Yes| Subscribe["Subscribe Event -> EventStore"]
Record --> |No| Skip["Skip Recording"]
Subscribe --> Run["run_replay(events)"]
Skip --> Run
Run --> Recover{"ResilientKernel?"}
Recover --> |Yes| Rebuild["Recover() -> Rebuild State"]
Recover --> |No| End([End])
Rebuild --> End
```

**Diagram sources**
- [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [ntrade/kernel/resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)

**Section sources**
- [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [ntrade/kernel/resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)

### EventBus Pub/Sub System
- Subscriptions support base-class handlers via MRO; handler exceptions are swallowed to isolate failures.
- Publish serializes dispatch with an RLock; maintains bounded history for replay/debugging.
- Used throughout kernel for decoupling producers/consumers (market data, signals, portfolio updates).

```mermaid
classDiagram
class EventBus {
-dict _subscribers
-deque _history
-RLock _lock
+subscribe(event_type, handler) Callable
+unsubscribe(event_type, handler) void
+publish(event) void
+history list
+clear() void
+__len__() int
}
```

**Diagram sources**
- [ntrade/kernel/event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

**Section sources**
- [ntrade/kernel/event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

### Engine Stack Processing
- MarketEngine projects raw events into instrument read-model state and emits normalized QuoteUpdatedEvent.
- StrategyEngine fans events to strategies via hooks; strategies emit signals which go through RiskEngine before becoming orders.
- OrderEngine routes intents to ExecutionRouter; execution targets publish fill/update events to PortfolioEngine.

```mermaid
sequenceDiagram
participant Bus as "EventBus"
participant ME as "MarketEngine"
participant SE as "StrategyEngine"
participant RE as "RiskEngine"
participant OE as "OrderEngine"
participant ER as "ExecutionRouter"
participant BE as "BrokerExecution/SimulatedExecution"
participant PE as "PortfolioEngine"
Bus->>ME : TickEvent/QuoteEvent/DepthEvent
ME->>Bus : QuoteUpdatedEvent
Bus->>SE : SignalGeneratedEvent
SE->>RE : Approve/Reject
RE-->>OE : Approved Intent
OE->>ER : OrderIntentEvent
ER->>BE : Place Order
BE-->>PE : PositionUpdatedEvent / BalanceChangedEvent
```

**Diagram sources**
- [ntrade/engines/market_engine.py:1-64](file://ntrade/engines/market_engine.py#L1-L64)
- [ntrade/engines/strategy_engine.py:1-102](file://ntrade/engines/strategy_engine.py#L1-L102)

**Section sources**
- [ntrade/engines/market_engine.py:1-64](file://ntrade/engines/market_engine.py#L1-L64)
- [ntrade/engines/strategy_engine.py:1-102](file://ntrade/engines/strategy_engine.py#L1-L102)

### Domain Model Hierarchy
- Base Instrument encapsulates state (quote, depth, history, stream, indicators, signals, metadata, session) and exposes capability objects for market, trade, stream, analytics, derivatives, and extension.
- Asset-specific classes specialize behavior and defaults (e.g., Equity, Index, ETF, Currency, Commodity, Bond, Crypto, Spot).
- Instruments apply events via apply_quote/apply_depth; hydrate metadata lazily from broker adapter.

```mermaid
classDiagram
class Instrument {
+symbol string
+exchange string
+name string
+currency string
+tick_size float
+lot_size int
+freeze_qty int
+broker_adapter BrokerAdapter
+broker BrokerExtensionFacade
+order OrderFacade
+market MarketCapability
+trade TradeCapability
+stream StreamCapability
+analytics AnalyticsCapability
+derivatives DerivativesCapability
+extension ExtensionCapability
+apply_quote(Quote) Instrument
+apply_depth(MarketDepth) Instrument
+refresh(force, now) Instrument
+hydrate() Instrument
+set_signal(name, value) Instrument
+get_signal(name, default) Any
+snapshot() dict
+clone() Instrument
}
class Equity {
+KIND string
+DEFAULT_EXCHANGE string
+market_cap float
}
class Index {
+KIND string
+DEFAULT_EXCHANGE string
}
class ETF {
+KIND string
+DEFAULT_EXCHANGE string
}
class Currency {
+KIND string
+DEFAULT_EXCHANGE string
}
class Commodity {
+KIND string
+DEFAULT_EXCHANGE string
}
class Bond {
+KIND string
+DEFAULT_EXCHANGE string
}
class Crypto {
+KIND string
+DEFAULT_EXCHANGE string
}
class Spot {
+KIND string
+DEFAULT_EXCHANGE string
}
Instrument <|-- Equity
Instrument <|-- Index
Instrument <|-- ETF
Instrument <|-- Currency
Instrument <|-- Commodity
Instrument <|-- Bond
Instrument <|-- Crypto
Instrument <|-- Spot
```

**Diagram sources**
- [ntrade/domain/instruments/base.py:1-305](file://ntrade/domain/instruments/base.py#L1-L305)
- [ntrade/domain/instruments/cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)

**Section sources**
- [ntrade/domain/instruments/base.py:1-305](file://ntrade/domain/instruments/base.py#L1-L305)
- [ntrade/domain/instruments/cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)

### Broker Abstraction Pattern and Capability System
- BrokerAdapter abstracts transport; implements connect/disconnect, quote/depth/history retrieval, order placement, lifecycle methods, streaming multiplexing, and optional portfolio endpoints.
- Capability system registers broker-specific features globally; instrument.broker.<capability>() resolves dynamically and fails fast if unsupported.
- Dependency injection: Instrument receives BrokerAdapter via constructor or lazy factory; kernel injects clock for zero-parity timestamps.

```mermaid
classDiagram
class BrokerAdapter {
<<abstract>>
+name string
+connect() BrokerAdapter
+disconnect() void
+connected bool
+get_quote(instrument) Quote
+get_depth(instrument) MarketDepth
+get_historical(instrument, timeframe, days, start, end) CandleSeries
+place_order(order) Order
+cancel_order(order) Order
+modify_order(order, **kw) Order
+get_instrument_metadata(instrument) dict
+subscribe(instrument) void
+unsubscribe(instrument) void
+_dispatch_tick(instrument, tick) void
+set_clock(clock) BrokerAdapter
}
class BrokerExtensionFacade {
-instrument Instrument
+available() string[]
+__getattr__(name) callable
}
class Capability {
+name string
+fn callable
+brokers tuple~string~
+supports(broker_name) bool
+invoke(instrument, *args, **kwargs) any
}
BrokerExtensionFacade --> Capability : "resolves"
```

**Diagram sources**
- [ntrade/brokers/base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [ntrade/brokers/capabilities.py:1-74](file://ntrade/brokers/capabilities.py#L1-L74)

**Section sources**
- [ntrade/brokers/base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [ntrade/brokers/capabilities.py:1-74](file://ntrade/brokers/capabilities.py#L1-L74)

### System Context: Data Flows from Sources to Execution
- Market data sources (live broker feed, replay store, synthetic simulator) produce canonical events.
- EventBus distributes events to engines; MarketEngine updates instrument read models; downstream engines compute candles, indicators, signals, risk checks, and order intents.
- ExecutionRouter selects appropriate execution target; fills update portfolio and publish balance changes.

```mermaid
graph TB
SRC["Sources<br/>DhanFeed / Replay / Synthetic"] --> BUS["EventBus"]
BUS --> MK["MarketEngine"]
MK --> QUOTE["QuoteUpdatedEvent"]
QUOTE --> CD["CandleEngine"]
QUOTE --> IN["IndicatorEngine"]
QUOTE --> ST["StrategyEngine"]
ST --> SIG["SignalGeneratedEvent"]
SIG --> RK["RiskEngine"]
RK --> ORD["OrderEngine"]
ORD --> RT["ExecutionRouter"]
RT --> EX["BrokerExecution / SimulatedExecution"]
EX --> PO["PortfolioEngine"]
```

**Diagram sources**
- [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [ntrade/engines/market_engine.py:1-64](file://ntrade/engines/market_engine.py#L1-L64)
- [ntrade/engines/strategy_engine.py:1-102](file://ntrade/engines/strategy_engine.py#L1-L102)

**Section sources**
- [ARCHITECTURE.md:156-215](file://ARCHITECTURE.md#L156-L215)

## Dependency Analysis
- Dependency direction: Public API depends on Kernel and Domain; Kernel depends on Engines, Execution, Storage; Domain depends on no Broker layer; Broker layer depends on Domain types only.
- Coupling: EventBus decouples engines; BrokerAdapter isolates transport; Capability system avoids conditional logic in domain.
- External dependencies: EventStore for persistence; Clock abstractions for deterministic time; optional broker transports hidden behind adapters.

```mermaid
graph LR
API["Public API"] --> KERNEL["TradingKernel"]
KERNEL --> ENGINES["Engines"]
KERNEL --> EXEC["ExecutionRouter"]
KERNEL --> STORE["EventStore"]
DOMAIN["Domain Layer"] --> |no import| BROKER["Broker Layer"]
BROKER --> DOMAIN
ENGINES --> DOMAIN
EXEC --> BROKER
```

**Diagram sources**
- [ntrade/facade.py:1-101](file://ntrade/facade.py#L1-L101)
- [ntrade/kernel/session.py:1-198](file://ntrade/kernel/session.py#L1-L198)
- [ntrade/brokers/base.py:1-163](file://ntrade/brokers/base.py#L1-L163)

**Section sources**
- [ARCHITECTURE.md:20-51](file://ARCHITECTURE.md#L20-L51)

## Performance Considerations
- EventBus serialization ensures thread-safe dispatch under concurrent producers (websocket callbacks vs runner loop).
- Immutable events and frozen dataclasses enable safe recording, replay, and comparison.
- Lazy hydration and cached properties reduce overhead until needed.
- Replay/backtest use deterministic clocks to avoid wall-clock variability.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
- Error isolation: EventBus swallows handler exceptions; one bad subscriber cannot crash the kernel.
- Resilience: ResilientKernel recovers state deterministically without re-trading; open-order deltas restored; execution sequence reseeded.
- Common issues: Missing broker capabilities raise AttributeError; ensure capability registered and supported by current broker.

**Section sources**
- [ntrade/kernel/event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)
- [ntrade/kernel/resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)
- [ntrade/brokers/capabilities.py:1-74](file://ntrade/brokers/capabilities.py#L1-L74)

## Conclusion
nTrade’s Clean Architecture separates concerns across layers, enabling interchangeable event sources and execution targets while preserving zero parity across live, replay, and backtest. The event-centric kernel, robust pub/sub system, rich domain model, and extensible broker abstraction provide a resilient, testable, and scalable foundation for institutional-grade trading systems.

[No sources needed since this section summarizes without analyzing specific files]