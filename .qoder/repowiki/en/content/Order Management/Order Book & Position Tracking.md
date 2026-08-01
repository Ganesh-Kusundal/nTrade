# Order Book & Position Tracking

<cite>
**Referenced Files in This Document**
- [book.py](file://ntrade/domain/orders/book.py)
- [order.py](file://ntrade/domain/orders/order.py)
- [portfolio.py](file://ntrade/domain/portfolio.py)
- [order_engine.py](file://ntrade/engines/order_engine.py)
- [position_sync.py](file://ntrade/engines/position_sync.py)
- [order_events.py](file://ntrade/events/order.py)
- [broker_executor.py](file://ntrade/execution/broker_executor.py)
- [router.py](file://ntrade/execution/router.py)
- [portfolio_engine.py](file://ntrade/engines/portfolio_engine.py)
- [paper_broker.py](file://ntrade/brokers/paper.py)
- [test_orders.py](file://tests/test_orders.py)
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
This document explains how the system manages open orders, tracks their lifecycle and partial fills, and integrates order execution with positions and portfolio updates. It covers the order book structure, operations to add/update/remove/query orders, and the relationship between order book entries and position changes. It also provides examples for querying and filtering orders by status or instrument, monitoring execution progress, and discusses performance considerations for large order books and efficient lookups.

## Project Structure
The order book and position tracking functionality spans domain models, engines, execution components, and broker adapters:
- Domain models define immutable order/trade book entries and rich order types.
- Engines materialize signals into order intents and reconcile live state from brokers.
- Execution components route intents to targets (simulated or real brokers), track open orders, and emit lifecycle events.
- Portfolio engine updates positions and balances on fills.
- Paper broker provides an in-memory implementation of order book and trade book retrieval.

```mermaid
graph TB
subgraph "Domain"
OB["OrderBook / TradeBook"]
O["Order + OrderFacade"]
P["Portfolio / Position / Account"]
end
subgraph "Engines"
OE["OrderEngine"]
PE["PortfolioEngine"]
PS["PositionSyncEngine"]
end
subgraph "Execution"
ER["ExecutionRouter"]
BE["BrokerExecution"]
end
subgraph "Brokers"
PB["PaperBroker"]
end
O --> OE
OE --> ER
ER --> BE
BE --> PB
BE --> |Events| PE
BE --> |Events| PS
PB --> OB
PB --> |"TradeBook"| OB
PE --> P
PS --> P
```

**Diagram sources**
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [portfolio_engine.py:1-69](file://ntrade/engines/portfolio_engine.py#L1-L69)
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [paper_broker.py:1-216](file://ntrade/brokers/paper.py#L1-L216)

**Section sources**
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [portfolio_engine.py:1-69](file://ntrade/engines/portfolio_engine.py#L1-L69)
- [paper_broker.py:1-216](file://ntrade/brokers/paper.py#L1-L216)

## Core Components
- OrderBook and TradeBook: Immutable collections of open orders and executed trades with symbol filtering and dict conversion utilities.
- Order model and facade: Rich order representation with lifecycle helpers and a convenient entry API bound to instruments.
- Portfolio, Position, Account: Read models for positions and holdings with computed metrics and refresh capabilities.
- OrderEngine: Converts approved signals into order intents and submits them via the router.
- BrokerExecution: Tracks open orders, polls broker for lifecycle updates, emits fill/rejection/timeout events, and supports modify/cancel.
- PortfolioEngine: Updates positions and account balance on fills; publishes position and balance change events.
- PositionSyncEngine: Reconciles broker-reported positions and cash into kernel read models, publishing canonical events.
- PaperBroker: In-memory broker implementing order placement, lifecycle, and order/trade book retrieval.

**Section sources**
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [portfolio_engine.py:1-69](file://ntrade/engines/portfolio_engine.py#L1-L69)
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)
- [paper_broker.py:1-216](file://ntrade/brokers/paper.py#L1-L216)

## Architecture Overview
The order lifecycle flows through signal approval, intent creation, routing, execution, and event-driven updates to positions and accounts. The broker executor maintains an in-memory open-order tracker and reconciles broker-reported states.

```mermaid
sequenceDiagram
participant Strat as "Strategy"
participant OE as "OrderEngine"
participant Router as "ExecutionRouter"
participant BE as "BrokerExecution"
participant Broker as "BrokerAdapter"
participant PE as "PortfolioEngine"
participant PS as "PositionSyncEngine"
Strat->>OE : SignalApprovedEvent
OE->>Router : OrderIntentEvent
Router->>BE : submit(intent)
BE->>Broker : place_order(order)
Broker-->>BE : Order (status/filled)
alt Synchronous fill
BE-->>PE : OrderFilledEvent
PE-->>PS : PositionUpdatedEvent + BalanceChangedEvent
else Asynchronous lifecycle
BE->>BE : poll()
BE->>Broker : get_order_status(order)
Broker-->>BE : Updated order
BE-->>PE : OrderFilledEvent (partial/full)
BE-->>OE : OrderUpdatedEvent / OrderRejectedEvent / OrderTimeoutEvent
PE-->>PS : PositionUpdatedEvent + BalanceChangedEvent
end
```

**Diagram sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [portfolio_engine.py:1-69](file://ntrade/engines/portfolio_engine.py#L1-L69)
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)

## Detailed Component Analysis

### Order Book Data Model
- OrderBookEntry captures symbol, order_id, side, quantity, price, status, exchange, timestamp.
- OrderBook is an immutable tuple of entries with symbol filtering and iteration helpers.
- TradeBookEntry captures executed trade details linked to an order_id.
- TradeBook mirrors OrderBook for executed trades.

```mermaid
classDiagram
class OrderBookEntry {
+string symbol
+string order_id
+string side
+int quantity
+float price
+string status
+string exchange
+datetime timestamp
+to_dict() dict
}
class OrderBook {
+tuple~OrderBookEntry~ entries
+datetime timestamp
+for_symbol(symbol) list
+to_dicts() list
+__len__() int
+__iter__() iterator
+__getitem__(index) OrderBookEntry
}
class TradeBookEntry {
+string symbol
+string trade_id
+string order_id
+string side
+int quantity
+float price
+datetime timestamp
+to_dict() dict
}
class TradeBook {
+tuple~TradeBookEntry~ entries
+datetime timestamp
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

### Order Model and Facade
- Order encapsulates instrument, side, quantity, type, prices, lifecycle flags, and execution metadata.
- OrderFacade provides concise buy/sell/market/stop/cover/bracket methods that delegate to the instrument’s broker adapter.

```mermaid
classDiagram
class Order {
+Instrument instrument
+OrderSide side
+int quantity
+OrderType order_type
+TradeType trade_type
+float price
+float trigger_price
+float target_price
+float stop_loss_price
+string order_id
+OrderStatus status
+int filled_qty
+float avg_price
+datetime created_at
+is_open bool
+is_filled bool
+cancel() Order
+modify(...) Order
+refresh() Order
+executed_price() float
+executed_price_and_time() (float,string)
+as_dict() dict
}
class OrderFacade {
-Instrument instrument
+buy(quantity, price, ...) Order
+sell(quantity, price, ...) Order
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

### Portfolio and Positions
- Position holds symbol, quantity, average price, last traded price, product, exchange, and metadata; exposes market_value and pnl.
- Portfolio aggregates positions and holdings, computes aggregate pnl and market value, and supports refresh from broker.
- Account holds balance and holdings with refresh capability.

```mermaid
classDiagram
class Position {
+string symbol
+int quantity
+float avg_price
+float ltp
+string product
+string exchange
+dict metadata
+market_value float
+pnl float
+as_dict() dict
}
class Holding {
+string symbol
+int quantity
+float avg_price
+float ltp
+dict metadata
+pnl float
+as_dict() dict
}
class Portfolio {
+Position[] positions
+Holding[] holdings
+symbol string
+pnl float
+live_pnl float
+market_value float
+position(symbol) Position?
+refresh() Portfolio
+as_dict() dict
}
class Account {
+float balance
+Holding[] holdings
+metadata dict
+holding(symbol) Holding?
+refresh() Account
+as_dict() dict
}
Portfolio --> Position : "contains"
Account --> Holding : "contains"
```

**Diagram sources**
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)

**Section sources**
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)

### Order Engine and Execution Routing
- OrderEngine subscribes to SignalApprovedEvent, creates OrderIntentEvent, and submits via ExecutionRouter.
- ExecutionRouter selects an execution target by strategy name or default.

```mermaid
sequenceDiagram
participant OE as "OrderEngine"
participant Bus as "EventBus"
participant Router as "ExecutionRouter"
OE->>Bus : subscribe(SignalApprovedEvent)
Bus-->>OE : SignalApprovedEvent(signal)
OE->>OE : build OrderIntentEvent
OE->>Bus : publish(OrderIntentEvent)
OE->>Router : submit(intent)
Router-->>OE : OrderRejectedEvent?
```

**Diagram sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)

**Section sources**
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)

### BrokerExecution: Open Orders, Partial Fills, and Lifecycle
- Maintains an in-memory map of open orders keyed by order_id, tracking filled quantities and status transitions.
- On submit: places order via instrument.order.place, publishes OrderAcceptedEvent, handles synchronous fills immediately.
- On poll: refreshes order status, detects timeouts, publishes OrderUpdatedEvent on status changes, and emits partial/full fills via OrderFilledEvent.
- Handles cancellation/rejection after partial fills by emitting rejection only for remaining quantity.
- Supports restore_open for crash recovery and modify/cancel operations.

```mermaid
flowchart TD
Start([Submit Intent]) --> Place["Place order via instrument.order.place"]
Place --> Accepted{"Order accepted?"}
Accepted --> |No| Reject["Publish OrderRejectedEvent"]
Accepted --> |Yes| FillCheck{"Synchronous fill?"}
FillCheck --> |Yes| EmitFill["Emit OrderFilledEvent"]
FillCheck --> |No| TrackOpen["Track in _open with intent/order/filled/status/placed_at"]
TrackOpen --> PollLoop["poll() loop over _open"]
PollLoop --> Refresh["get_order_status(order)"]
Refresh --> Timeout{"PENDING > threshold?"}
Timeout --> |Yes| TimeoutEvt["Publish OrderTimeoutEvent"]
Timeout --> |No| StatusChange{"Status changed?"}
StatusChange --> |Yes| UpdateEvt["Publish OrderUpdatedEvent"]
StatusChange --> |No| Next
Next --> EmitPartial["Compute new fill qty = current - already"]
EmitPartial --> NewFill{"new_qty > 0?"}
NewFill --> |Yes| EmitFill2["Publish OrderFilledEvent(new_qty)"]
NewFill --> |No| Terminal{"Terminal status?"}
Terminal --> |COMPLETED| Remove["Remove from _open"]
Terminal --> |REJECTED/CANCELLED| Remaining{"Remaining > 0?"}
Remaining --> |Yes| EmitReject["Publish OrderRejectedEvent(remaining)"]
Remaining --> |No| Remove
EmitFill --> End([Done])
EmitFill2 --> End
Remove --> End
EmitReject --> End
Reject --> End
```

**Diagram sources**
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)

**Section sources**
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)

### PortfolioEngine: Fills to Positions and Balances
- Subscribes to OrderFilledEvent and nets position quantities, re-averages entry price, and debits/credits cash including commission and statutory charges.
- Publishes PositionUpdatedEvent and BalanceChangedEvent for downstream consumers.

```mermaid
sequenceDiagram
participant BE as "BrokerExecution"
participant PE as "PortfolioEngine"
participant Port as "Portfolio"
participant Acc as "Account"
BE-->>PE : OrderFilledEvent
PE->>Port : position(symbol)
alt New position
PE->>Port : append Position
else Existing position
PE->>Port : update quantity and avg_price
end
PE->>Acc : debit/credit balance (notional + charges)
PE-->>PE : publish PositionUpdatedEvent
PE-->>PE : publish BalanceChangedEvent
```

**Diagram sources**
- [portfolio_engine.py:1-69](file://ntrade/engines/portfolio_engine.py#L1-L69)

**Section sources**
- [portfolio_engine.py:1-69](file://ntrade/engines/portfolio_engine.py#L1-L69)

### PositionSyncEngine: Live Reconciliation
- Periodically pulls positions and balance from the broker, reconciling local read models and publishing canonical events.
- Ensures transient failures do not wipe state; keeps previous values on error.

```mermaid
flowchart TD
SyncStart(["sync()"]) --> SafePos["_safe_positions()"]
SafePos --> PosOk{"Positions returned?"}
PosOk --> |No| KeepState["Return existing count"]
PosOk --> |Yes| Upsert["Upsert broker positions into portfolio"]
Upsert --> DropLocal["Drop local positions not reported"]
DropLocal --> SafeBal["_safe_balance()"]
SafeBal --> BalOk{"Balance returned?"}
BalOk --> |No| SkipBal["Skip balance update"]
BalOk --> |Yes| UpdateBal["Update account.balance if changed"]
UpdateBal --> PublishBal["Publish BalanceChangedEvent"]
SkipBal --> Done(["Done"])
PublishBal --> Done
KeepState --> Done
```

**Diagram sources**
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)

**Section sources**
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)

### PaperBroker: Order Book and Trade Book Retrieval
- Implements order placement, lifecycle, and retrieval of OrderBook and TradeBook.
- get_orderbook returns all stored orders as immutable OrderBookEntry tuples.
- get_trade_book returns completed orders as TradeBookEntry tuples.

```mermaid
sequenceDiagram
participant Client as "Caller"
participant PB as "PaperBroker"
Client->>PB : get_orderbook()
PB-->>Client : OrderBook(entries=...)
Client->>PB : get_trade_book()
PB-->>Client : TradeBook(entries=...)
```

**Diagram sources**
- [paper_broker.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)

**Section sources**
- [paper_broker.py:1-216](file://ntrade/brokers/paper.py#L1-L216)
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)

### Conceptual Overview
Conceptually, the order book represents open orders per instrument with statuses such as PENDING, PARTIALLY_FILLED, COMPLETED, REJECTED, CANCELLED. Partial fills incrementally increase filled_qty while leaving remaining quantity open until completion or cancellation. Each fill triggers position and balance updates, ensuring consistency across the portfolio read models.

```mermaid
flowchart TD
A["New Order"] --> B["Open in OrderBook"]
B --> C{"Partial Fill?"}
C --> |Yes| D["Increase filled_qty<br/>Keep remaining open"]
C --> |No| E{"Full Fill?"}
E --> |Yes| F["Mark COMPLETED<br/>Remove from OrderBook"]
E --> |No| G["Await more fills"]
D --> H["Publish OrderFilledEvent"]
F --> H
H --> I["Update Position & Balance"]
I --> J["Publish PositionUpdatedEvent + BalanceChangedEvent"]
```

[No sources needed since this diagram shows conceptual workflow, not actual code structure]

## Dependency Analysis
The following diagram maps key dependencies among core modules involved in order book management and position tracking.

```mermaid
graph TB
OB["domain.orders.book"] --> PB["brokers.paper"]
O["domain.orders.order"] --> OE["engines.order_engine"]
O --> BE["execution.broker_executor"]
PB --> OB
BE --> |Events| PE["engines.portfolio_engine"]
BE --> |Events| PS["engines.position_sync"]
PE --> P["domain.portfolio"]
PS --> P
OE --> ER["execution.router"]
```

**Diagram sources**
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [paper_broker.py:1-216](file://ntrade/brokers/paper.py#L1-L216)

**Section sources**
- [book.py:1-120](file://ntrade/domain/orders/book.py#L1-L120)
- [order.py:1-173](file://ntrade/domain/orders/order.py#L1-L173)
- [portfolio.py:1-173](file://ntrade/domain/portfolio.py#L1-L173)
- [order_engine.py:1-34](file://ntrade/engines/order_engine.py#L1-L34)
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [router.py:1-49](file://ntrade/execution/router.py#L1-L49)
- [paper_broker.py:1-216](file://ntrade/brokers/paper.py#L1-L216)

## Performance Considerations
- OrderBook and TradeBook are immutable tuples of dataclasses, enabling thread-safe reads and minimal overhead during iteration and filtering.
- Symbol filtering uses simple list comprehensions; for very large books, consider indexing by symbol in higher layers if frequent queries dominate.
- BrokerExecution maintains an in-memory open-order dictionary keyed by order_id, providing O(1) lookup for status polling and partial fill tracking.
- Polling frequency should be tuned to avoid excessive broker calls; stale detection evicts unresponsive orders after a threshold.
- PortfolioEngine updates are event-driven and localized per fill, minimizing contention and avoiding full scans except for position lookup by symbol.
- PaperBroker constructs OrderBook/TradeBook snapshots on demand; caching may be beneficial if repeated queries occur within short intervals.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
- No broker-backed instrument: Order submission returns OrderRejectedEvent when the instrument lacks a broker adapter.
- Missing order_id handling: If the broker returns a missing or invalid order_id, a unique fallback id is generated to prevent collisions.
- Stale orders: Excessive poll failures lead to eviction from the open-order tracker to free resources.
- Timeouts: PENDING orders older than a threshold trigger OrderTimeoutEvent for monitoring and remediation.
- Partial fill then cancellation/rejection: Only the remaining unfilled quantity is rejected; previously filled shares remain emitted.
- Transient broker errors: PositionSyncEngine preserves existing state on failure to avoid wiping positions or zeroing balances.

**Section sources**
- [broker_executor.py:1-262](file://ntrade/execution/broker_executor.py#L1-L262)
- [position_sync.py:1-110](file://ntrade/engines/position_sync.py#L1-L110)
- [order_events.py:1-91](file://ntrade/events/order.py#L1-L91)

## Conclusion
The system provides a robust, event-driven architecture for order book management and position tracking. Immutable domain models ensure safe data sharing, while the execution layer tracks open orders, handles partial fills, and reconciles broker state. Portfolio updates are consistent and incremental, driven by fills and periodic reconciliation. For large-scale deployments, careful tuning of polling, filtering strategies, and potential indexing can optimize performance.

[No sources needed since this section summarizes without analyzing specific files]