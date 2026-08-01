# Event Bus System

<cite>
**Referenced Files in This Document**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [base.py](file://ntrade/events/base.py)
- [market.py](file://ntrade/events/market.py)
- [order.py](file://ntrade/events/order.py)
- [portfolio.py](file://ntrade/events/portfolio.py)
- [risk.py](file://ntrade/events/risk.py)
- [lifecycle.py](file://ntrade/events/lifecycle.py)
- [__init__.py](file://ntrade/events/__init__.py)
- [session.py](file://ntrade/kernel/session.py)
- [market_engine.py](file://ntrade/engines/market_engine.py)
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
This document explains the EventBus implementation and its event-driven communication mechanism that decouples trading components in the nTrade kernel. The system uses a publish-subscribe pattern where subsystems publish typed events and subscribe to handlers, enabling flexible, resilient, and deterministic interactions across market data processing, order lifecycle, portfolio updates, risk checks, and kernel lifecycle management.

The EventBus is synchronous and thread-safe, with serialized dispatch via a reentrant lock. It supports handler registration by event type (including inheritance-based routing), error isolation per handler, and an in-memory history for replay and monitoring. Events are immutable dataclasses with deterministic timestamps sourced from the TradingClock, ensuring identical behavior across live, replay, and backtest modes.

## Project Structure
The event bus and event types are organized under:
- Kernel layer: EventBus implementation and wiring within the TradingKernel
- Events layer: Base event model and domain-specific event classes
- Engines: Consumers/producers of events that wire handlers and publish derived events

```mermaid
graph TB
subgraph "Kernel"
EB["EventBus"]
TK["TradingKernel"]
end
subgraph "Events"
EBase["Event (base)"]
EM["Market Events"]
EO["Order Events"]
EP["Portfolio Events"]
ER["Risk Events"]
EL["Lifecycle Events"]
end
subgraph "Engines"
ME["MarketEngine"]
OE["OrderEngine"]
PE["PortfolioEngine"]
end
TK --> EB
ME --> EB
OE --> EB
PE --> EB
EM --> EBase
EO --> EBase
EP --> EBase
ER --> EBase
EL --> EBase
```

**Diagram sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [base.py](file://ntrade/events/base.py)
- [market.py](file://ntrade/events/market.py)
- [order.py](file://ntrade/events/order.py)
- [portfolio.py](file://ntrade/events/portfolio.py)
- [risk.py](file://ntrade/events/risk.py)
- [lifecycle.py](file://ntrade/events/lifecycle.py)
- [session.py](file://ntrade/kernel/session.py)
- [market_engine.py](file://ntrade/engines/market_engine.py)

**Section sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [base.py](file://ntrade/events/base.py)
- [session.py](file://ntrade/kernel/session.py)
- [market_engine.py](file://ntrade/engines/market_engine.py)

## Core Components
- EventBus: Synchronous, thread-safe pub/sub bus with MRO-based routing, per-handler error isolation, and bounded history.
- Event base class: Immutable dataclass carrying a deterministic timestamp and unique identifier.
- Domain events: Typed events for market data, orders, portfolio, risk, and lifecycle.

Key responsibilities:
- EventBus.subscribe(event_type, handler): Register handlers; returns the handler for convenience.
- EventBus.unsubscribe(event_type, handler): Remove a previously registered handler.
- EventBus.publish(event): Serialize dispatch over MRO, record event in history, isolate handler exceptions.

Event typing and metadata:
- All events inherit from Event and carry ts (datetime) and event_id (unique hex).
- Some events include additional fields such as metadata (e.g., signal events).

**Section sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [base.py](file://ntrade/events/base.py)
- [__init__.py](file://ntrade/events/__init__.py)

## Architecture Overview
The EventBus sits at the center of the kernel, connecting engines and external producers/consumers. Handlers subscribe to specific event types or base classes, receiving subclass events through MRO traversal. Publishing is serialized to ensure consistent state transitions and prevent torn reads when multiple threads produce events.

```mermaid
sequenceDiagram
participant Producer as "Producer (e.g., MarketEngine)"
participant Bus as "EventBus"
participant H1 as "Handler A"
participant H2 as "Handler B"
participant Store as "Optional EventStore"
Producer->>Bus : publish(Event)
Bus->>Bus : append to history
Bus->>H1 : call handler(Event)
alt Handler raises
Bus-->>Bus : log error, continue
end
Bus->>H2 : call handler(Event)
alt Handler raises
Bus-->>Bus : log error, continue
end
Note over Bus,Store : If subscribed to base Event, store records all events
```

**Diagram sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [session.py](file://ntrade/kernel/session.py)

**Section sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [session.py](file://ntrade/kernel/session.py)

## Detailed Component Analysis

### EventBus Class
Responsibilities:
- Maintain subscriber maps keyed by event type
- Provide subscribe/unsubscribe APIs
- Publish events with MRO-based routing
- Record events into a bounded deque history
- Expose thread-safe accessors for history and length

Implementation highlights:
- Thread safety: RLock serializes subscription changes and publishing
- Error isolation: Each handler invocation is wrapped in try/except; exceptions are logged and do not stop other handlers
- History: Fixed-size deque ensures memory bounds; accessible via property
- Clearing: Clears both subscribers and history atomically

```mermaid
flowchart TD
Start(["publish(event)"]) --> Lock["Acquire RLock"]
Lock --> Record["Append event to history"]
Record --> IterateMRO["Iterate type(event).__mro__"]
IterateMRO --> ForEachType{"Handlers for klass?"}
ForEachType --> |Yes| CallHandler["Call handler(event)"]
CallHandler --> TryCatch{"Exception?"}
TryCatch --> |Yes| LogError["Log error and continue"]
TryCatch --> |No| NextHandler["Next handler"]
ForEachType --> |No| NextType["Next class in MRO"]
NextHandler --> NextType
NextType --> IterateMRO
IterateMRO --> Done["Release RLock"]
Done --> End(["Return"])
```

**Diagram sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)

**Section sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)

### Event Base Class and Typing System
- Event is a frozen dataclass with keyword-only fields: ts (datetime) and event_id (auto-generated unique string)
- Immutability ensures deterministic recording and replay
- Timestamps must come from the TradingClock to maintain zero parity across modes

```mermaid
classDiagram
class Event {
+datetime ts
+string event_id
}
class TickEvent {
+string symbol
+string exchange
+float price
+int quantity
+string side
+string kind
}
class QuoteEvent {
+string symbol
+string exchange
+float ltp
+float bid
+float ask
+float open
+float high
+float low
+float prev_close
+int volume
+int oi
}
class DepthEvent {
+string symbol
+string exchange
+tuple bids
+tuple asks
}
class CandleClosedEvent {
+string symbol
+string exchange
+string timeframe
+float open
+float high
+float low
+float close
+int volume
}
class QuoteUpdatedEvent {
+string symbol
+string exchange
+float ltp
+float bid
+float ask
}
class IndicatorUpdatedEvent {
+string symbol
+string exchange
+string timeframe
+dict indicators
}
Event <|-- TickEvent
Event <|-- QuoteEvent
Event <|-- DepthEvent
Event <|-- CandleClosedEvent
Event <|-- QuoteUpdatedEvent
Event <|-- IndicatorUpdatedEvent
```

**Diagram sources**
- [base.py](file://ntrade/events/base.py)
- [market.py](file://ntrade/events/market.py)

**Section sources**
- [base.py](file://ntrade/events/base.py)
- [market.py](file://ntrade/events/market.py)

### Order Lifecycle Events
These events represent the flow from signal approval to execution outcomes:
- OrderIntentEvent: Pre-execution intent created after risk approval
- OrderAcceptedEvent: Execution target accepted the order
- OrderRejectedEvent: Execution target or risk rejected the order
- OrderFilledEvent: Fill confirmation with price and costs
- OrderUpdatedEvent: Status changes for open orders
- OrderTimeoutEvent: Pending order exceeded timeout threshold

```mermaid
classDiagram
class OrderIntentEvent {
+string symbol
+string exchange
+string side
+int quantity
+string order_type
+float price
+string strategy
}
class OrderAcceptedEvent {
+string order_id
+string symbol
+string exchange
+string side
+int quantity
+string strategy
}
class OrderRejectedEvent {
+string order_id
+string symbol
+string exchange
+string side
+int quantity
+string reason
+string strategy
}
class OrderFilledEvent {
+string order_id
+string symbol
+string exchange
+string side
+int quantity
+float fill_price
+float commission
+float statutory
+string strategy
}
class OrderUpdatedEvent {
+string order_id
+string symbol
+string exchange
+string side
+string status
+int filled_qty
+float avg_price
+string strategy
}
class OrderTimeoutEvent {
+string order_id
+string symbol
+string exchange
+string side
+int quantity
+float age_seconds
+string strategy
}
Event <|-- OrderIntentEvent
Event <|-- OrderAcceptedEvent
Event <|-- OrderRejectedEvent
Event <|-- OrderFilledEvent
Event <|-- OrderUpdatedEvent
Event <|-- OrderTimeoutEvent
```

**Diagram sources**
- [order.py](file://ntrade/events/order.py)

**Section sources**
- [order.py](file://ntrade/events/order.py)

### Portfolio Events
- PositionUpdatedEvent: Reflects net position changes after fills
- BalanceChangedEvent: Account balance updates after fills or cash movements

```mermaid
classDiagram
class PositionUpdatedEvent {
+string symbol
+string exchange
+int quantity
+float avg_price
+float ltp
}
class BalanceChangedEvent {
+float balance
}
Event <|-- PositionUpdatedEvent
Event <|-- BalanceChangedEvent
```

**Diagram sources**
- [portfolio.py](file://ntrade/events/portfolio.py)

**Section sources**
- [portfolio.py](file://ntrade/events/portfolio.py)

### Risk Events
- SignalGeneratedEvent: Strategy decision with trade parameters and optional metadata
- SignalApprovedEvent: Risk passed the signal; OMS may materialize it
- SignalRejectedEvent: Risk blocked the signal with reason
- RiskHaltedEvent: Circuit breaker tripped; trading must stop
- RiskResumedEvent: Circuit breaker cleared; trading may resume

```mermaid
classDiagram
class SignalGeneratedEvent {
+string symbol
+string exchange
+string side
+int quantity
+float price
+string strategy
+dict metadata
}
class SignalApprovedEvent {
+SignalGeneratedEvent signal
}
class SignalRejectedEvent {
+SignalGeneratedEvent signal
+string reason
}
class RiskHaltedEvent {
+string reason
+float equity
}
class RiskResumedEvent {
+string reason
}
Event <|-- SignalGeneratedEvent
Event <|-- SignalApprovedEvent
Event <|-- SignalRejectedEvent
Event <|-- RiskHaltedEvent
Event <|-- RiskResumedEvent
```

**Diagram sources**
- [risk.py](file://ntrade/events/risk.py)

**Section sources**
- [risk.py](file://ntrade/events/risk.py)

### Lifecycle Events
- KernelStartedEvent: Kernel finished wiring and is live
- SessionStartedEvent: Trading session began
- SessionStoppedEvent: Session ended with flush/close
- RunnerStartedEvent: LiveRunner began orchestration loop
- RunnerStoppedEvent: LiveRunner stopped and cleaned up
- HeartbeatEvent: Periodic heartbeat proving kernel liveness
- FeedDisconnectedEvent: Market feed websocket disconnected

```mermaid
classDiagram
class KernelStartedEvent {
+string mode
}
class SessionStartedEvent {
+string session_id
}
class SessionStoppedEvent {
+string session_id
+string reason
}
class RunnerStartedEvent
class RunnerStoppedEvent {
+string reason
}
class HeartbeatEvent {
+int tick_count
+int open_orders
}
class FeedDisconnectedEvent {
+string reason
}
Event <|-- KernelStartedEvent
Event <|-- SessionStartedEvent
Event <|-- SessionStoppedEvent
Event <|-- RunnerStartedEvent
Event <|-- RunnerStoppedEvent
Event <|-- HeartbeatEvent
Event <|-- FeedDisconnectedEvent
```

**Diagram sources**
- [lifecycle.py](file://ntrade/events/lifecycle.py)

**Section sources**
- [lifecycle.py](file://ntrade/events/lifecycle.py)

### Handler Registration Patterns and Event Routing
- Subscribe by exact type or base class: Subscribers to a base class receive all subclasses via MRO traversal
- Handler signature: Callable[[Event], None]
- Unsubscribe removes a previously registered handler safely
- Example usage patterns:
  - Engines subscribe to raw market events and publish normalized events
  - Optional full-event recording subscribes to base Event for audit/replay

```mermaid
sequenceDiagram
participant Engine as "MarketEngine"
participant Bus as "EventBus"
participant Store as "EventStore (optional)"
Engine->>Bus : subscribe(TickEvent, on_tick)
Engine->>Bus : subscribe(QuoteEvent, on_quote)
Engine->>Bus : subscribe(DepthEvent, on_depth)
Store->>Bus : subscribe(Event, _record)
Note over Engine,Store : Handlers registered; any published event routed by MRO
```

**Diagram sources**
- [market_engine.py](file://ntrade/engines/market_engine.py)
- [session.py](file://ntrade/kernel/session.py)

**Section sources**
- [market_engine.py](file://ntrade/engines/market_engine.py)
- [session.py](file://ntrade/kernel/session.py)

## Dependency Analysis
The EventBus depends on the Event base class and is used extensively by engines and the kernel. Engines subscribe to specific event types and publish derived events, forming a clear producer-consumer graph.

```mermaid
graph TB
EB["EventBus"]
EBase["Event"]
ME["MarketEngine"]
OE["OrderEngine"]
PE["PortfolioEngine"]
TK["TradingKernel"]
EBase --> EB
ME --> EB
OE --> EB
PE --> EB
TK --> EB
```

**Diagram sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [base.py](file://ntrade/events/base.py)
- [market_engine.py](file://ntrade/engines/market_engine.py)
- [session.py](file://ntrade/kernel/session.py)

**Section sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [base.py](file://ntrade/events/base.py)
- [market_engine.py](file://ntrade/engines/market_engine.py)
- [session.py](file://ntrade/kernel/session.py)

## Performance Considerations
- Synchronous vs asynchronous processing:
  - EventBus is synchronous; dispatch occurs within the same thread context
  - Serialization via RLock prevents concurrent modifications and ensures consistent state
- Handler priority:
  - Dispatch follows most-derived-first order using type(event).__mro__
  - No explicit priority field; ordering is determined by Python’s MRO
- Memory management:
  - History is a bounded deque with configurable maxlen; older events are dropped automatically
  - Clear() resets subscribers and history atomically
- Throughput:
  - Minimal overhead per publish: append to deque and iterate matching handlers
  - Exception handling per handler avoids cascading failures but adds logging overhead

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and remedies:
- Handler exceptions:
  - Exceptions are caught and logged; check logs for handler names and event types
  - Isolate faulty handlers to prevent bus disruption
- Missing handlers:
  - Ensure correct event type registration; verify MRO if subscribing to base classes
- Memory growth:
  - Adjust max_history during EventBus initialization to bound memory usage
- Replay and audit:
  - Subscribe to base Event to capture all events for debugging and replay

**Section sources**
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [session.py](file://ntrade/kernel/session.py)

## Conclusion
The EventBus provides a robust, deterministic, and decoupled communication backbone for the nTrade kernel. Its synchronous, thread-safe design, MRO-based routing, error isolation, and bounded history make it suitable for high-frequency trading environments where reliability and reproducibility are critical. By adhering to the event typing system and handler registration patterns, developers can build scalable, modular trading systems with clear separation of concerns.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Concrete Examples

- Custom event creation:
  - Define a new dataclass inheriting from Event with keyword-only fields
  - Use deterministic timestamps from the TradingClock
  - Include metadata where appropriate for traceability

- Handler registration:
  - Subscribe engines to relevant event types
  - Optionally subscribe to base Event for full recording

- Event flow debugging:
  - Inspect history via bus.history
  - Monitor logs for handler exceptions
  - Use session-level start/stop events to correlate lifecycle phases

[No sources needed since this section provides general guidance]