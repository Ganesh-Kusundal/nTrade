# Order Lifecycle Management

<cite>
**Referenced Files in This Document**
- [order.py](file://ntrade/domain/orders/order.py)
- [base.py](file://ntrade/brokers/base.py)
- [paper.py](file://ntrade/brokers/paper.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [broker_executor.py](file://ntrade/execution/broker_executor.py)
- [order_events.py](file://ntrade/events/order.py)
- [test_orders.py](file://tests/test_orders.py)
- [cash.py](file://ntrade/domain/instruments/cash.py)
- [derivatives.py](file://ntrade/domain/instruments/derivatives.py)
- [base_instrument.py](file://ntrade/domain/instruments/base.py)
</cite>

## Update Summary
**Changes Made**
- Added comprehensive documentation for automatic trade_type derivation feature (K-024)
- Updated OrderFacade method signatures to reflect new `trade_type=None` parameter
- Added detailed explanation of instrument-kind-aware default behavior
- Enhanced examples to demonstrate both automatic and explicit trade_type usage
- Updated architecture diagrams to show the new _default_trade_type() function
- Added troubleshooting guidance for automatic trade_type derivation issues

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
This document explains the Order class and its complete lifecycle management within the system. It covers the data model, enumerations, facade methods for order placement with automatic trade_type derivation, and the broker-driven lifecycle transitions from submission to completion or cancellation. The system now intelligently determines the appropriate trading segment (CNC vs MIS) based on instrument characteristics, eliminating the need for manual trade_type specification in most cases.

The K-024 enhancement introduces automatic TradeType derivation that analyzes instrument KIND attributes to determine whether cash instruments (equities, ETFs, spot) should default to CNC (delivery) or derivatives (futures, options) should default to MIS (intraday margin).

## Project Structure
The order domain is defined in a single module that exposes the core data model and facade with intelligent trade_type derivation. Broker adapters implement the transport boundary between the domain and live brokers. The execution layer orchestrates asynchronous order polling and event emission.

```mermaid
graph TB
subgraph "Domain"
O["Order (dataclass)"]
OS["OrderSide"]
OT["OrderType"]
TT["TradeType"]
ST["OrderStatus"]
OF["OrderFacade"]
DT["_default_trade_type()"]
end
subgraph "Instrument Types"
EQ["Equity (KIND=equity)"]
ETF["ETF (KIND=etf)"]
SP["Spot (KIND=spot)"]
FD["Future (KIND=future)"]
OP["Option (KIND=option)"]
IDX["Index (KIND=index)"]
end
subgraph "Broker Layer"
BA["BrokerAdapter (abstract)"]
PB["PaperBroker"]
DB["DhanBroker"]
end
subgraph "Execution"
BE["BrokerExecution"]
OE["Order Events"]
end
OF --> O
O --> BA
DT --> OF
EQ --> DT
ETF --> DT
SP --> DT
FD --> DT
OP --> DT
IDX --> DT
BA --> PB
BA --> DB
BE --> BA
BE --> OE
```

**Diagram sources**
- [order.py:14-191](file://ntrade/domain/orders/order.py#L14-L191)
- [cash.py:8-49](file://ntrade/domain/instruments/cash.py#L8-L49)
- [derivatives.py:16-111](file://ntrade/domain/instruments/derivatives.py#L16-L111)
- [base_instrument.py:47-51](file://ntrade/domain/instruments/base.py#L47-L51)
- [base.py:25-163](file://ntrade/brokers/base.py#L25-L163)
- [paper.py:23-200](file://ntrade/brokers/paper.py#L23-L200)
- [dhan.py:54-200](file://ntrade/brokers/dhan.py#L54-L200)
- [broker_executor.py:46-200](file://ntrade/execution/broker_executor.py#L46-L200)
- [order_events.py:1-51](file://ntrade/events/order.py#L1-L51)

**Section sources**
- [order.py:1-191](file://ntrade/domain/orders/order.py#L1-L191)
- [cash.py:1-50](file://ntrade/domain/instruments/cash.py#L1-L50)
- [derivatives.py:1-200](file://ntrade/domain/instruments/derivatives.py#L1-L200)
- [base_instrument.py:1-200](file://ntrade/domain/instruments/base.py#L1-L200)
- [base.py:1-163](file://ntrade/brokers/base.py#L1-L163)
- [paper.py:1-200](file://ntrade/brokers/paper.py#L1-L200)
- [dhan.py:1-200](file://ntrade/brokers/dhan.py#L1-L200)
- [broker_executor.py:1-200](file://ntrade/execution/broker_executor.py#L1-L200)
- [order_events.py:1-51](file://ntrade/events/order.py#L1-L51)

## Core Components
- Order dataclass fields: instrument, side, quantity, order_type, trade_type, price, trigger_price, target_price, stop_loss_price, order_id, status, filled_qty, avg_price, created_at.
- Enums:
  - OrderSide: BUY, SELL
  - OrderType: LIMIT, MARKET, STOP_LIMIT, STOP_MARKET, COVER, BRACKET
  - TradeType: MIS, CNC, MARGIN, MTF
  - OrderStatus: PENDING, COMPLETED, REJECTED, CANCELLED, PARTIALLY_FILLED
- OrderFacade methods with automatic trade_type derivation: buy(), sell(), limit(), market(), stop(), cover(), bracket(), place()
- Automatic trade_type derivation: _default_trade_type() function that determines appropriate trading segment based on instrument kind

**Updated** The system now automatically derives the correct trade_type based on instrument characteristics, eliminating manual configuration for common scenarios. Cash instruments (equity, etf, spot) default to CNC (delivery), while derivatives (future, option, index) default to MIS (intraday margin).

**Section sources**
- [order.py:14-191](file://ntrade/domain/orders/order.py#L14-L191)

## Architecture Overview
The OrderFacade constructs an Order with intelligent trade_type derivation and delegates placement to the instrument's broker adapter. The _default_trade_type() function analyzes the instrument's KIND attribute to determine whether to use CNC (delivery) for cash instruments or MIS (intraday margin) for derivatives. The BrokerExecution layer handles asynchronous order tracking, polling for updates, and publishing lifecycle events.

```mermaid
sequenceDiagram
participant User as "Caller"
participant Facade as "OrderFacade"
participant Default as "_default_trade_type()"
participant Order as "Order"
participant Broker as "BrokerAdapter"
participant Exec as "BrokerExecution"
participant Bus as "Event Bus"
User->>Facade : buy()/sell()/limit()/market()/stop()/cover()/bracket()
Note over Facade : If trade_type=None
Facade->>Default : _default_trade_type(instrument)
Default-->>Facade : TradeType.CNC/MIS based on instrument KIND
Facadе->>Order : construct with derived trade_type
Facade->>Broker : place_order(order)
Broker-->>Facade : order with id/status
Facade-->>User : Order instance
Note over Exec,Bus : Asynchronous lifecycle
Exec->>Broker : get_order_status(order)
Broker-->>Exec : updated order state
Exec->>Bus : publish OrderUpdatedEvent / OrderFilledEvent / OrderRejectedEvent
```

**Diagram sources**
- [order.py:124-191](file://ntrade/domain/orders/order.py#L124-L191)
- [cash.py:8-49](file://ntrade/domain/instruments/cash.py#L8-L49)
- [derivatives.py:16-111](file://ntrade/domain/instruments/derivatives.py#L16-L111)
- [base.py:96-122](file://ntrade/brokers/base.py#L96-L122)
- [paper.py:108-159](file://ntrade/brokers/paper.py#L108-L159)
- [broker_executor.py:58-169](file://ntrade/execution/broker_executor.py#L58-L169)
- [order_events.py:1-51](file://ntrade/events/order.py#L1-L51)

## Detailed Component Analysis

### Order Data Model and Status Transitions
- Fields:
  - instrument: linked Instrument object used to resolve broker adapter
  - side: OrderSide enum
  - quantity: int
  - order_type: OrderType enum
  - trade_type: TradeType enum (now auto-derived when None)
  - price: float (limit price)
  - trigger_price: float (stop trigger)
  - target_price: float (bracket target leg)
  - stop_loss_price: float (bracket stop leg)
  - order_id: str | None
  - status: OrderStatus enum
  - filled_qty: int
  - avg_price: float
  - created_at: datetime | None
- Properties:
  - is_open: True when status is PENDING or PARTIALLY_FILLED
  - is_filled: True when status is COMPLETED
- Lifecycle methods:
  - cancel(): calls broker.cancel_order(order)
  - modify(): calls broker.modify_order(order, ...)
  - refresh(): calls broker.get_order_status(order)
  - executed_price(): returns broker-reported average price or local avg_price
  - executed_price_and_time(): returns (price, time) tuple from broker or defaults

```mermaid
stateDiagram-v2
[*] --> PENDING
PENDING --> PARTIALLY_FILLED : "partial fill"
PARTIALLY_FILLED --> COMPLETED : "fully filled"
PENDING --> COMPLETED : "full fill"
PENDING --> CANCELLED : "cancel"
PENDING --> REJECTED : "rejected"
PARTIALLY_FILLED --> CANCELLED : "cancel after partial"
PARTIALLY_FILLED --> REJECTED : "reject after partial"
COMPLETED --> [*]
CANCELLED --> [*]
REJECTED --> [*]
```

**Diagram sources**
- [order.py:35-67](file://ntrade/domain/orders/order.py#L35-L67)

**Section sources**
- [order.py:43-116](file://ntrade/domain/orders/order.py#L43-L116)

### Automatic Trade Type Derivation (K-024)
The system now includes intelligent trade_type derivation based on instrument characteristics:

- **_default_trade_type() function**: Analyzes instrument KIND to determine appropriate trading segment
- **Cash instruments** (equity, etf, spot): Default to CNC (delivery/trading segment)
- **Derivatives** (future, option, index): Default to MIS (intraday margin)
- **Explicit override**: Users can still specify trade_type explicitly to override automatic derivation

```mermaid
flowchart TD
Start(["OrderFacade.place"]) --> CheckTradeType{"trade_type is None?"}
CheckTradeType --> |Yes| CallDefault["_default_trade_type(instrument)"]
CheckTradeType --> |No| UseExplicit["Use provided trade_type"]
CallDefault --> CheckKind{"instrument.KIND in ['equity','etf','spot']?"}
CheckKind --> |Yes| SetCNC["Set trade_type = CNC"]
CheckKind --> |No| SetMIS["Set trade_type = MIS"]
SetCNC --> BuildOrder["Build Order(dataclass)"]
SetMIS --> BuildOrder
UseExplicit --> BuildOrder
BuildOrder --> CheckBroker{"Has broker_adapter?"}
CheckBroker --> |No| RaiseErr["Raise RuntimeError"]
CheckBroker --> |Yes| CallPlace["broker.place_order(order)"]
CallPlace --> ReturnOrder["Return Order"]
```

**Diagram sources**
- [order.py:124-191](file://ntrade/domain/orders/order.py#L124-L191)

**Section sources**
- [order.py:118-132](file://ntrade/domain/orders/order.py#L118-L132)
- [order.py:172-191](file://ntrade/domain/orders/order.py#L172-L191)

### OrderFacade Methods and Usage Patterns
All OrderFacade methods now support automatic trade_type derivation:

- buy(quantity, price=0.0, order_type=LIMIT, trade_type=None, trigger_price=0.0)
- sell(quantity, price=0.0, order_type=LIMIT, trade_type=None, trigger_price=0.0)
- limit(side, quantity, price, **kw)
- market(side, quantity, **kw)
- stop(side, quantity, price, trigger_price, **kw)
- cover(side, quantity, price=0.0, trigger_price=0.0, **kw)
- bracket(side, quantity, price, target_price, stop_loss_price, **kw)
- place(side, quantity, order_type=LIMIT, trade_type=None, price=0.0, trigger_price=0.0, **kwargs)

Usage patterns:
- Construct an Order via a convenience method (buy/sell/limit/market/stop/cover/bracket).
- All methods normalize string inputs to enums and delegate to place().
- place() automatically derives trade_type if None, otherwise uses explicit value.
- place() constructs the Order and invokes broker.place_order(order).

**Updated** All facade methods now accept optional trade_type parameter that defaults to None, enabling automatic derivation. When trade_type is None, the system calls _default_trade_type() to determine the appropriate trading segment based on the instrument's KIND attribute.

**Section sources**
- [order.py:134-191](file://ntrade/domain/orders/order.py#L134-L191)

### BrokerAdapter Contract and Implementations
- Abstract methods and defaults:
  - place_order(order): required
  - cancel_order(order), modify_order(order, ...), get_order_status(order), get_executed_price(order), get_executed_price_and_time(order): optional with default behaviors
- PaperBroker:
  - place_order: instant fill for MARKET or uses provided price; sets COMPLETED immediately
  - cancel_order: marks open orders as CANCELLED
  - modify_order: updates stored order fields
  - get_order_status: syncs order fields from internal store
- DhanBroker:
  - Implements full broker integration; order placement and status retrieval follow SEBI rules and provider constraints

```mermaid
classDiagram
class BrokerAdapter {
+connect()
+place_order(order) Order
+cancel_order(order) Order
+modify_order(order, price, quantity, order_type, trigger_price) Order
+get_order_status(order) Order
+get_executed_price(order) float
+get_executed_price_and_time(order) (float, string)
}
class PaperBroker {
+place_order(order) Order
+cancel_order(order) Order
+modify_order(order, ...) Order
+get_order_status(order) Order
}
class DhanBroker {
+connect()
+get_quote(instrument) Quote
+get_historical(...)
+... order methods ...
}
BrokerAdapter <|-- PaperBroker
BrokerAdapter <|-- DhanBroker
```

**Diagram sources**
- [base.py:25-163](file://ntrade/brokers/base.py#L25-L163)
- [paper.py:108-159](file://ntrade/brokers/paper.py#L108-L159)
- [dhan.py:54-200](file://ntrade/brokers/dhan.py#L54-L200)

**Section sources**
- [base.py:25-163](file://ntrade/brokers/base.py#L25-L163)
- [paper.py:108-159](file://ntrade/brokers/paper.py#L108-L159)
- [dhan.py:54-200](file://ntrade/brokers/dhan.py#L54-L200)

### Execution Layer and Event Emission
- BrokerExecution.submit(intent):
  - Builds Order via instrument.order.place(...)
  - Publishes OrderAcceptedEvent immediately
  - If synchronous fill (is_filled), emits fill event; otherwise tracks in _open
- BrokerExecution.poll():
  - Refreshes order status via broker.get_order_status(order)
  - Emits OrderUpdatedEvent on status changes
  - Emits OrderFilledEvent or OrderRejectedEvent based on final state
  - Handles timeouts and stale entries

```mermaid
sequenceDiagram
participant Exec as "BrokerExecution"
participant Broker as "BrokerAdapter"
participant Bus as "Event Bus"
Exec->>Broker : get_order_status(order)
Broker-->>Exec : updated order
Exec->>Bus : publish OrderUpdatedEvent(status changed)
alt fully filled
Exec->>Bus : publish OrderFilledEvent
else rejected/cancelled
Exec->>Bus : publish OrderRejectedEvent(remaining qty if any)
end
```

**Diagram sources**
- [broker_executor.py:58-169](file://ntrade/execution/broker_executor.py#L58-L169)
- [order_events.py:1-51](file://ntrade/events/order.py#L1-L51)

**Section sources**
- [broker_executor.py:58-169](file://ntrade/execution/broker_executor.py#L58-L169)
- [order_events.py:1-51](file://ntrade/events/order.py#L1-L51)

### Examples and Best Practices
- Creating different order types with automatic trade_type:
  - Limit buy (equity → CNC): use rel.order.buy(quantity, price)
  - Market sell (derivative → MIS): use rel.order.market(OrderSide.SELL, quantity)
  - Stop-limit (automatic derivation): use rel.order.stop(OrderSide.SELL, quantity, price, trigger_price)
  - Cover order (automatic derivation): use rel.order.cover(OrderSide.SELL, quantity, price, trigger_price)
  - Bracket order (automatic derivation): use rel.order.bracket(OrderSide.BUY, quantity, price, target_price, stop_loss_price)
- Explicit trade_type override:
  - Force MIS on equity: use rel.order.buy(quantity, price, trade_type=TradeType.MIS)
  - Force CNC on derivative: use rel.order.buy(quantity, price, trade_type=TradeType.CNC)
- Monitoring order status:
  - Use order.refresh() to pull latest status from broker
  - Observe OrderUpdatedEvent via the event bus for real-time updates
- Handling partial fills:
  - Check order.is_open and order.filled_qty vs order.quantity
  - On poll(), handle remaining quantities for rejection or further fills
- Managing modifications:
  - Use order.modify(price=..., quantity=..., order_type=..., trigger_price=...)
  - Ensure order is still open before modification
- Error handling:
  - RuntimeError raised if no broker adapter is present
  - Catch exceptions during broker calls and handle gracefully
  - Respect SEBI constraints (e.g., F&O market orders converted to LIMIT by DhanBroker)

**Updated** Examples now demonstrate both automatic trade_type derivation and explicit override scenarios. The automatic derivation eliminates the need for manual trade_type specification in most cases, while explicit overrides provide flexibility for edge cases.

**Section sources**
- [test_orders.py:97-153](file://tests/test_orders.py#L97-L153)
- [order.py:134-191](file://ntrade/domain/orders/order.py#L134-L191)
- [dhan.py:54-200](file://ntrade/brokers/dhan.py#L54-L200)

## Dependency Analysis
- Order depends on Instrument through the instrument field to access broker_adapter.
- OrderFacade depends on Order and Instrument to construct and place orders.
- _default_trade_type() depends on Instrument KIND attribute to determine appropriate trade_type.
- BrokerAdapter defines the interface; PaperBroker and DhanBroker implement it.
- BrokerExecution depends on BrokerAdapter and publishes events to the bus.

```mermaid
graph LR
Instrument["Instrument"] --> Order["Order"]
Instrument --> Default["Default Trade Type"]
Default --> OrderFacade["OrderFacade"]
OrderFacade --> Order
Order --> BrokerAdapter["BrokerAdapter"]
BrokerAdapter --> PaperBroker["PaperBroker"]
BrokerAdapter --> DhanBroker["DhanBroker"]
BrokerExecution["BrokerExecution"] --> BrokerAdapter
BrokerExecution --> Events["Order Events"]
```

**Diagram sources**
- [order.py:14-191](file://ntrade/domain/orders/order.py#L14-L191)
- [cash.py:8-49](file://ntrade/domain/instruments/cash.py#L8-L49)
- [derivatives.py:16-111](file://ntrade/domain/instruments/derivatives.py#L16-L111)
- [base.py:25-163](file://ntrade/brokers/base.py#L25-L163)
- [paper.py:23-200](file://ntrade/brokers/paper.py#L23-L200)
- [dhan.py:54-200](file://ntrade/brokers/dhan.py#L54-L200)
- [broker_executor.py:46-200](file://ntrade/execution/broker_executor.py#L46-L200)
- [order_events.py:1-51](file://ntrade/events/order.py#L1-L51)

**Section sources**
- [order.py:14-191](file://ntrade/domain/orders/order.py#L14-L191)
- [cash.py:8-49](file://ntrade/domain/instruments/cash.py#L8-L49)
- [derivatives.py:16-111](file://ntrade/domain/instruments/derivatives.py#L16-L111)
- [base.py:25-163](file://ntrade/brokers/base.py#L25-L163)
- [paper.py:23-200](file://ntrade/brokers/paper.py#L23-L200)
- [dhan.py:54-200](file://ntrade/brokers/dhan.py#L54-L200)
- [broker_executor.py:46-200](file://ntrade/execution/broker_executor.py#L46-L200)
- [order_events.py:1-51](file://ntrade/events/order.py#L1-L51)

## Performance Considerations
- Prefer using OrderFacade convenience methods to reduce boilerplate and ensure consistent normalization.
- Automatic trade_type derivation adds minimal overhead but eliminates manual configuration errors.
- Avoid frequent refresh() calls; batch status checks where possible.
- In live environments, rely on BrokerExecution.poll() for efficient status updates and event emission.
- For backtesting/paper trading, PaperBroker provides deterministic behavior without network latency.

**Updated** Automatic trade_type derivation provides better performance by eliminating manual configuration while maintaining accuracy. The _default_trade_type() function performs simple string comparison operations that have negligible computational cost compared to network I/O operations.

## Troubleshooting Guide
- RuntimeError when placing/modifying orders:
  - Occurs if the instrument has no broker_adapter configured. Ensure the instrument is created with a valid broker.
- Orders not filling:
  - Verify order_type and price settings; MARKET orders may require available liquidity.
  - For F&O instruments, DhanBroker converts MARKET to LIMIT per SEBI rules.
  - Check that automatic trade_type derivation selected the correct segment (CNC vs MIS).
- Partial fills:
  - Monitor filled_qty and status; handle remaining quantities appropriately.
- Cancel/Modify failures:
  - Ensure the order is still open (PENDING or PARTIALLY_FILLED); terminal orders cannot be modified.
- Stale orders:
  - BrokerExecution.evicts orders after repeated status-refresh failures; re-check connectivity and broker availability.
- Unexpected trade_type:
  - Verify instrument KIND attribute; check if explicit trade_type override is needed.
  - Review _default_trade_type() logic for custom instrument types.
  - Ensure the instrument class properly defines the KIND constant.

**Updated** Added troubleshooting guidance for automatic trade_type derivation issues. Common problems include incorrect KIND constants in custom instrument classes or unexpected behavior when mixing cash and derivative instruments in the same strategy.

**Section sources**
- [order.py:69-105](file://ntrade/domain/orders/order.py#L69-L105)
- [order.py:118-132](file://ntrade/domain/orders/order.py#L118-L132)
- [base.py:96-122](file://ntrade/brokers/base.py#L96-L122)
- [paper.py:121-159](file://ntrade/brokers/paper.py#L121-L159)
- [broker_executor.py:134-169](file://ntrade/execution/broker_executor.py#L134-L169)
- [test_orders.py:38-45](file://tests/test_orders.py#L38-L45)

## Conclusion
The Order class and OrderFacade provide a clean, extensible interface for order placement across multiple brokers with intelligent trade_type derivation. The automatic trade_type system (K-024) eliminates manual configuration for common scenarios while allowing explicit overrides when needed. The BrokerAdapter abstraction ensures consistent behavior between paper and live environments, while BrokerExecution manages asynchronous lifecycle events. By following the documented patterns for order creation, monitoring, and error handling, developers can build robust trading systems with predictable order behavior and reduced configuration complexity.

**Updated** The addition of automatic trade_type derivation significantly improves usability while maintaining flexibility for advanced use cases. Developers can now focus on trading logic rather than instrument-specific configuration details, while still having full control when needed through explicit trade_type parameters.