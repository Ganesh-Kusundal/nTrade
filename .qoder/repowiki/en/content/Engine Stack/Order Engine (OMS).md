# Order Engine (OMS)

<cite>
**Referenced Files in This Document**
- [order_engine.py](file://ntrade/engines/order_engine.py)
- [router.py](file://ntrade/execution/router.py)
- [broker_executor.py](file://ntrade/execution/broker_executor.py)
- [base.py](file://ntrade/brokers/base.py)
- [paper.py](file://ntrade/brokers/paper.py)
- [order.py](file://ntrade/domain/orders/order.py)
- [book.py](file://ntrade/domain/orders/book.py)
- [order_events.py](file://ntrade/events/order.py)
- [event_bus.py](file://ntrade/kernel/event_bus.py)
- [test_order_state_events.py](file://tests/test_order_state_events.py)
- [test_orders.py](file://tests/test_orders.py)
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

## Introduction
This document provides comprehensive documentation for the Order Engine (Order Management System, OMS) that manages the complete order lifecycle: placement, modification, cancellation, and fill processing. It explains order state transitions, order book maintenance, trade history tracking, advanced order types (Market, Limit, Stop, Cover, Bracket), partial fill handling, idempotent operations, order routing, fill reconciliation, and audit trail generation. The system is event-driven and broker-agnostic, ensuring zero-parity across paper, backtest, and live environments.

## Project Structure
The OMS spans several layers:
- Domain models define orders, order types, statuses, and immutable books for open orders and executed trades.
- Engines translate approved signals into order intents and route them to execution targets.
- Execution layer handles submission, polling, timeouts, partial fills, and cost accounting.
- Broker adapters abstract transport details and provide order lifecycle methods.
- Event bus ensures reliable, serialized dispatch of lifecycle events.

```mermaid
graph TB
subgraph "Domain"
O["Order Model<br/>order.py"]
B["Books<br/>book.py"]
end
subgraph "Engines"
OE["OrderEngine<br/>order_engine.py"]
end
subgraph "Execution"
ER["ExecutionRouter<br/>router.py"]
BE["BrokerExecution<br/>broker_executor.py"]
end
subgraph "Brokers"
BA["BrokerAdapter<br/>base.py"]
PB["PaperBroker<br/>paper.py"]
end
subgraph "Events"
EV["Order Events<br/>order_events.py"]
EB["EventBus<br/>event_bus.py"]
end
OE --> ER
ER --> BE
BE --> BA
BA --> PB
OE --> EB
BE --> EB
O --> BA
B --> BA
```

**Diagram sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

**Section sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

## Core Components
- OrderModel and OrderFacade: Rich order model with lifecycle helpers and instrument-bound entry points for placing Market, Limit, Stop, Cover, and Bracket orders.
- OrderEngine: Consumes approved signals, materializes OrderIntentEvent, publishes it, and submits via router; republishes rejections.
- ExecutionRouter: Routes intents to execution targets by strategy name or default target.
- BrokerExecution: Live execution engine that places orders, tracks open orders, polls status, emits fills/rejections/updates/timeouts, and supports crash recovery.
- BrokerAdapter and PaperBroker: Abstract broker interface and an in-memory implementation used for tests/backtests.
- Order/Trade Books: Immutable value objects representing open orders and executed trades.
- Events: Canonical event types for order lifecycle and base event model with kernel-clock timestamps.
- EventBus: Synchronous publish/subscribe bus with serialization and history.

**Section sources**
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

## Architecture Overview
The OMS follows a clean separation between domain, engines, execution, and brokers, connected by an event bus. Signals become order intents, which are routed to execution targets. For live trading, BrokerExecution coordinates asynchronous broker lifecycles, while for paper/backtesting, PaperBroker provides synchronous fills.

```mermaid
sequenceDiagram
participant Strategy as "Strategy"
participant Bus as "EventBus"
participant OMS as "OrderEngine"
participant Router as "ExecutionRouter"
participant Exec as "BrokerExecution"
participant Broker as "BrokerAdapter/PaperBroker"
Strategy->>Bus : Publish SignalApprovedEvent
Bus-->>OMS : on_signal_approved(event)
OMS->>Bus : Publish OrderIntentEvent
OMS->>Router : submit(intent)
Router->>Exec : submit(intent)
Exec->>Broker : place_order(order)
alt Synchronous fill
Exec->>Bus : Publish OrderAcceptedEvent
Exec->>Bus : Publish OrderFilledEvent
else Asynchronous fill
Exec->>Bus : Publish OrderAcceptedEvent
loop Poll
Exec->>Broker : get_order_status(order)
alt Status changed
Exec->>Bus : Publish OrderUpdatedEvent
end
alt Fill delta > 0
Exec->>Bus : Publish OrderFilledEvent
end
alt Terminal (REJECTED/CANCELLED/COMPLETED)
Exec->>Bus : Publish OrderRejectedEvent if needed
Exec->>Bus : Remove from open tracker
end
end
end
```

**Diagram sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

## Detailed Component Analysis

### Order Model and Facade
- Order dataclass encapsulates side, quantity, type, prices, status, and fill metrics. Provides helper properties for open/filled checks and broker-backed lifecycle methods (cancel, modify, refresh, executed price).
- OrderFacade binds order creation to an Instrument, exposing convenient methods for buy/sell, limit/market/stop/cover/bracket. Enforces type normalization and delegates placement to the instrument’s broker adapter.

```mermaid
classDiagram
class Order {
+instrument
+side
+quantity
+order_type
+trade_type
+price
+trigger_price
+target_price
+stop_loss_price
+order_id
+status
+filled_qty
+avg_price
+created_at
+is_open() bool
+is_filled() bool
+cancel() Order
+modify(price, quantity, order_type, trigger_price) Order
+refresh() Order
+executed_price() float
+executed_price_and_time() (float, string)
+as_dict() dict
}
class OrderFacade {
+instrument
+buy(quantity, price, order_type, trade_type, trigger_price) Order
+sell(quantity, price, order_type, trade_type, trigger_price) Order
+limit(side, quantity, price, **kw) Order
+market(side, quantity, **kw) Order
+stop(side, quantity, price, trigger_price, **kw) Order
+cover(side, quantity, price, trigger_price, **kw) Order
+bracket(side, quantity, price, target_price, stop_loss_price, **kw) Order
+place(side, quantity, order_type, trade_type, price, trigger_price, **kwargs) Order
}
OrderFacade --> Order : "creates"
```

**Diagram sources**
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)

**Section sources**
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)

### OrderBook and TradeBook
- Immutable frozen dataclasses represent collections of open orders and executed trades. Provide symbol filtering and conversion utilities. Used by broker adapters to expose consistent views.

```mermaid
classDiagram
class OrderBookEntry {
+symbol
+order_id
+side
+quantity
+price
+status
+exchange
+timestamp
+to_dict() dict
}
class OrderBook {
+entries
+timestamp
+for_symbol(symbol) list
+to_dicts() list
+__len__() int
+__iter__() iterator
+__getitem__(index) OrderBookEntry
}
class TradeBookEntry {
+symbol
+trade_id
+order_id
+side
+quantity
+price
+timestamp
+to_dict() dict
}
class TradeBook {
+entries
+timestamp
+for_symbol(symbol) list
+to_dicts() list
+__len__() int
+__iter__() iterator
+__getitem__(index) TradeBookEntry
}
OrderBook --> OrderBookEntry : "contains"
TradeBook --> TradeBookEntry : "contains"
```

**Diagram sources**
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)

**Section sources**
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)

### OrderEngine
- Subscribes to SignalApprovedEvent, constructs OrderIntentEvent, publishes it, and submits via router. Republishes any rejection outcome.

```mermaid
flowchart TD
Start(["on_signal_approved"]) --> BuildIntent["Build OrderIntentEvent"]
BuildIntent --> PublishIntent["Publish OrderIntentEvent"]
PublishIntent --> Submit["router.submit(intent)"]
Submit --> Outcome{"Outcome is rejection?"}
Outcome --> |Yes| PublishReject["Publish OrderRejectedEvent"]
Outcome --> |No| End(["Done"])
```

**Diagram sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)

**Section sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)

### ExecutionRouter
- Maintains named execution targets and a default fallback. Resolves target by intent.strategy; returns OrderRejectedEvent when no target exists.

```mermaid
flowchart TD
Start(["submit(intent)"]) --> Lookup["Lookup target by strategy"]
Lookup --> Found{"Target found?"}
Found --> |No| DefaultCheck{"Default set?"}
DefaultCheck --> |Yes| UseDefault["Use default target"]
DefaultCheck --> |No| Reject["Return OrderRejectedEvent"]
UseDefault --> CallSubmit["target.submit(intent)"]
Found --> |Yes| CallSubmit
CallSubmit --> Return(["Return result"])
Reject --> Return
```

**Diagram sources**
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)

**Section sources**
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)

### BrokerExecution (Live OMS)
- Places orders via instrument.order.place, publishes OrderAcceptedEvent immediately, and either emits synchronous fills or tracks open orders for asynchronous polling.
- poll() refreshes order status, publishes updates, computes partial fill deltas, emits fills and rejections, detects timeouts, and removes terminal orders.
- restore_open() rebuilds open-order state from event store deltas after crash recovery.
- modify/cancel delegate to broker adapter for open orders.

```mermaid
sequenceDiagram
participant Exec as "BrokerExecution"
participant Broker as "BrokerAdapter"
participant Bus as "EventBus"
Exec->>Broker : place_order(order)
Exec->>Bus : Publish OrderAcceptedEvent
alt Synchronous fill
Exec->>Bus : Publish OrderFilledEvent
else Asynchronous
Exec->>Exec : Track open order
loop poll()
Exec->>Broker : get_order_status(order)
Exec->>Bus : Publish OrderUpdatedEvent if status changed
Exec->>Exec : Compute new fill delta
Exec->>Bus : Publish OrderFilledEvent if delta > 0
alt Terminal
Exec->>Bus : Publish OrderRejectedEvent if needed
Exec->>Exec : Remove from open tracker
end
end
end
```

**Diagram sources**
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)

**Section sources**
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)

### BrokerAdapter and PaperBroker
- BrokerAdapter defines the contract for market data, order lifecycle, and portfolio access. Timestamps are resolved via an injected clock for zero-parity.
- PaperBroker implements in-memory behavior: instant fills for market/limit orders, order tracking, order/trade books, and simple balance/positions.

```mermaid
classDiagram
class BrokerAdapter {
+name
+connect() BrokerAdapter
+disconnect() void
+connected bool
+get_quote(instrument) Quote
+get_depth(instrument) MarketDepth?
+get_historical(instrument, timeframe, days, start, end) CandleSeries
+get_option_chain(underlying, expiry, num_strikes, **kwargs)
+place_order(order) Order
+cancel_order(order) Order
+modify_order(order, price, quantity, order_type, trigger_price) Order
+get_order_status(order) Order
+get_order_detail(order_id) dict
+get_executed_price(order) float
+get_executed_price_and_time(order) (float, string)
+get_instrument_metadata(instrument) dict
+get_orderbook() OrderBook
+get_trade_book() TradeBook
+order_report()
+get_live_pnl() float
+get_balance() float
+get_positions()
+get_holdings()
+subscribe(instrument) void
+unsubscribe(instrument) void
}
class PaperBroker {
+seed_quote(symbol, ltp, **kw) Quote
+seed_history(symbol, rows, timeframe, start_price) DataFrame
+connect() BrokerAdapter
+get_quote(instrument) Quote
+get_depth(instrument) MarketDepth
+get_historical(instrument, timeframe, days, start, end) CandleSeries
+get_option_chain(underlying, expiry, num_strikes, **kwargs) OptionChain
+place_order(order) Order
+cancel_order(order) Order
+modify_order(order, price, quantity, order_type, trigger_price) Order
+get_order_status(order) Order
+get_order_detail(order_id) dict
+get_orderbook() OrderBook
+get_trade_book() TradeBook
+order_report() dict
+orders list
+get_live_pnl() float
+get_balance() float
+get_positions() list
+get_holdings() list
+push_tick(instrument, price, side) void
}
PaperBroker --|> BrokerAdapter
```

**Diagram sources**
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)

**Section sources**
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)

### Events and EventBus
- Order lifecycle events include Intent, Accepted, Rejected, Filled, Updated, and Timeout. All extend a base Event with kernel-clock timestamp and unique event_id.
- EventBus provides thread-safe publish/subscribe with handler isolation and event history for replay.

```mermaid
classDiagram
class Event {
+ts datetime
+event_id string
}
class OrderIntentEvent {
+symbol
+exchange
+side
+quantity
+order_type
+price
+strategy
}
class OrderAcceptedEvent {
+order_id
+symbol
+exchange
+side
+quantity
+strategy
}
class OrderRejectedEvent {
+order_id
+symbol
+exchange
+side
+quantity
+reason
+strategy
}
class OrderFilledEvent {
+order_id
+symbol
+exchange
+side
+quantity
+fill_price
+commission
+statutory
+strategy
}
class OrderUpdatedEvent {
+order_id
+symbol
+exchange
+side
+status
+filled_qty
+avg_price
+strategy
}
class OrderTimeoutEvent {
+order_id
+symbol
+exchange
+side
+quantity
+age_seconds
+strategy
}
Event <|-- OrderIntentEvent
Event <|-- OrderAcceptedEvent
Event <|-- OrderRejectedEvent
Event <|-- OrderFilledEvent
Event <|-- OrderUpdatedEvent
Event <|-- OrderTimeoutEvent
```

**Diagram sources**
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

**Section sources**
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

### Order State Transitions and Partial Fill Handling
- States: PENDING -> PARTIALLY_FILLED -> COMPLETED; also REJECTED and CANCELLED terminal states.
- Partial fills are emitted incrementally; only the delta since last poll is published, ensuring idempotency.
- Timeouts: PENDING orders older than threshold emit OrderTimeoutEvent.
- Crash recovery: restore_open reconstructs open-order trackers from event store deltas, preventing duplicate fill emissions.

```mermaid
stateDiagram-v2
[*] --> PENDING
PENDING --> PARTIALLY_FILLED : "partial fill"
PARTIALLY_FILLED --> COMPLETED : "full fill"
PENDING --> REJECTED : "rejection"
PENDING --> CANCELLED : "cancel"
PARTIALLY_FILLED --> REJECTED : "cancel/reject remaining"
PARTIALLY_FILLED --> CANCELLED : "cancel remaining"
COMPLETED --> [*]
REJECTED --> [*]
CANCELLED --> [*]
```

**Diagram sources**
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)

**Section sources**
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)

### Advanced Order Types
- Market: Immediate fill at current LTP (paper) or best available price (live).
- Limit: Fill at specified price or better.
- Stop (Stop-Limit): Triggered when price reaches trigger_price; then placed as limit.
- Cover (CO): Entry with attached stop-loss leg.
- Bracket (BO): Entry with both target and stop-loss legs.

These are exposed via OrderFacade methods and enforced by broker adapters where required (e.g., converting F&O market orders to limit per regulations).

**Section sources**
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [test_orders.py:1-90](file://tests/test_orders.py#L1-L90)

### Order Routing Examples
- Strategy emits signal -> OrderEngine creates intent -> ExecutionRouter selects target by strategy -> BrokerExecution places order.
- If no target matches, OrderRejectedEvent is returned immediately.

**Section sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)

### Fill Reconciliation and Audit Trail
- Each fill includes commission and statutory costs computed against notional and product schedule.
- OrderUpdatedEvent captures status changes, filled quantities, and average price.
- EventBus maintains event history for replay and auditing.

**Section sources**
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

## Dependency Analysis
The OMS components have clear dependencies:
- OrderEngine depends on events and router.
- ExecutionRouter depends on registered targets (BrokerExecution).
- BrokerExecution depends on BrokerAdapter and events.
- PaperBroker implements BrokerAdapter.
- Order model interacts with BrokerAdapter via instrument.broker_adapter.
- EventBus underpins all event-driven interactions.

```mermaid
graph TB
OE["OrderEngine"] --> ER["ExecutionRouter"]
ER --> BE["BrokerExecution"]
BE --> BA["BrokerAdapter"]
BA --> PB["PaperBroker"]
OE --> EV["Order Events"]
BE --> EV
EV --> EB["EventBus"]
O["Order Model"] --> BA
```

**Diagram sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

**Section sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [event_bus.py:1-81](file://ntrade/kernel/event_bus.py#L1-L81)

## Performance Considerations
- EventBus serializes dispatch using a reentrant lock to prevent torn state across producers.
- BrokerExecution limits stale polling failures before evicting orders to avoid memory leaks.
- Partial fill emission computes deltas to minimize redundant events.
- PaperBroker uses in-memory structures for fast testing and backtesting.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- No execution target for strategy: Ensure ExecutionRouter has a target registered for the intent’s strategy or a default target.
- Missing broker adapter: Order placement requires an instrument with a broker adapter; otherwise, RuntimeError is raised.
- Stale orders: Excessive polling failures lead to eviction; check broker connectivity and error handling.
- Duplicate IDs: When brokers return missing or invalid IDs, BrokerExecution assigns BRK- fallback IDs to avoid collisions.
- Partial fill re-emission: Idempotent design ensures only new fill deltas are emitted; verify open-order tracking state.

**Section sources**
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [broker_executor.py:1-290](file://ntrade/execution/broker_executor.py#L1-L290)
- [test_order_state_events.py:1-109](file://tests/test_order_state_events.py#L1-L109)

## Conclusion
The Order Engine provides a robust, event-driven OMS that manages the full order lifecycle across paper, backtest, and live environments. It enforces zero-parity through kernel-clock timestamps, supports advanced order types, handles partial fills idempotently, and maintains comprehensive audit trails. The modular architecture ensures easy extension and integration with different brokers while preserving reliability and performance.