# Dhan Broker Implementation

<cite>
**Referenced Files in This Document**
- [dhan.py](file://ntrade/brokers/dhan.py)
- [dhan_auth.py](file://ntrade/brokers/dhan_auth.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [dhan_mapper.py](file://ntrade/brokers/dhan_mapper.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [base.py](file://ntrade/brokers/base.py)
- [order.py](file://ntrade/domain/orders/order.py)
- [quote.py](file://ntrade/domain/market/quote.py)
- [portfolio.py](file://ntrade/domain/portfolio.py)
- [retry.py](file://ntrade/execution/retry.py)
- [test_dhan_broker.py](file://tests/test_dhan_broker.py)
- [test_dhan_auth_unit.py](file://tests/test_dhan_auth_unit.py)
- [test_contract_auth_observability.py](file://tests/test_contract_auth_observability.py)
- [check_connection.py](file://check_connection.py)
</cite>

## Update Summary
**Changes Made**
- Enhanced authentication lifecycle documentation to clarify independence from observability
- Updated stop() method documentation to emphasize it only cancels token-refresh timer
- Added explicit contract documentation for auth-observability separation
- Updated architecture diagrams to reflect the clear separation between auth lifecycle and event publishing
- Enhanced troubleshooting section with guidance on auth shutdown behavior

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
This document explains the DhanBroker implementation that integrates with the Dhan trading platform via the Dhan-Tradehull library. The implementation follows a clean separation of concerns with a transport layer abstraction pattern. It covers authentication (PIN+TOTP verification, token lifecycle), transport abstraction for REST and WebSocket-based market data, mapping from Dhan wire formats to domain objects, order placement across multiple order types, partial fill handling, idempotent operations, position synchronization, portfolio updates, balance tracking, and resilience patterns such as rate limiting and error recovery tailored to Dhan's API constraints.

The architecture has been refactored to delegate all data calls through DhanTransport while maintaining the same public API surface, reducing the broker class complexity and improving maintainability. **Critical architectural principle**: The authentication lifecycle is completely independent from observability - stopping authentication never emits heartbeat, feed, or order events, ensuring clean separation between connection management and system monitoring.

## Project Structure
The Dhan integration is organized into a clear separation of concerns following the provider pattern:
- Authentication lifecycle and credential storage via DhanAuthProvider
- Transport layer wrapping Tradehull calls with retry and normalization via DhanTransport  
- Mapper layer converting Dhan responses to domain models via DhanMapper
- Broker adapter implementing the broker interface and orchestrating components via DhanBroker
- Domain models for orders, quotes, positions, and holdings
- Resilience utilities for retries and rate limiting

```mermaid
graph TB
subgraph "Broker Layer"
A["DhanBroker<br/>(dhan.py)<br/>~755 lines"]
B["DhanAuthProvider<br/>(dhan_auth_provider.py)<br/>Authentication + Auto-refresh"]
C["DhanTransport<br/>(dhan_transport.py)<br/>API Calls + Retry"]
D["DhanMapper<br/>(dhan_mapper.py)<br/>Data Normalization"]
end
subgraph "Auth & Credentials"
E["get_tradehull()<br/>(dhan_auth.py)<br/>Token Management"]
end
subgraph "Domain Models"
F["Order / OrderType / OrderStatus<br/>(order.py)"]
G["Quote / Tick<br/>(quote.py)"]
H["Position / Holding / Portfolio / Account<br/>(portfolio.py)"]
end
subgraph "Resilience"
I["RetryPolicy / RateLimiter<br/>(retry.py)"]
end
subgraph "Observability Contract"
J["No Event Bus<br/>Independent Lifecycle"]
end
A --> B
A --> C
A --> D
B --> E
C --> D
C --> I
A --> F
A --> G
A --> H
A -.-> J
B -.-> J
```

**Diagram sources**
- [dhan.py:51-75](file://ntrade/brokers/dhan.py#L51-L75)
- [dhan_auth_provider.py:28-88](file://ntrade/brokers/dhan_auth_provider.py#L28-88)
- [dhan_transport.py:49-79](file://ntrade/brokers/dhan_transport.py#L49-L79)
- [dhan_mapper.py:35-73](file://ntrade/brokers/dhan_mapper.py#L35-L73)
- [dhan_auth.py:114-167](file://ntrade/brokers/dhan_auth.py#L114-L167)
- [order.py:14-41](file://ntrade/domain/orders/order.py#L14-L41)
- [quote.py:9-28](file://ntrade/domain/market/quote.py#L9-L28)
- [portfolio.py:19-61](file://ntrade/domain/portfolio.py#L19-L61)
- [retry.py:19-68](file://ntrade/execution/retry.py#L19-L68)

**Section sources**
- [dhan.py:51-75](file://ntrade/brokers/dhan.py#L51-L75)
- [base.py:25-69](file://ntrade/brokers/base.py#L25-L69)

## Core Components
- **DhanBroker**: Implements the BrokerAdapter interface for Dhan, orchestrating auth, transport, and mapping. Now delegates all data calls through DhanTransport while maintaining the same public API surface. Handles quote retrieval, historical data, option chains, order placement/cancellation/modification, order/trade books, positions, holdings, and balances. **Critical**: The broker has no event bus and publishes no observability events during lifecycle operations.
- **DhanAuthProvider**: Manages authentication lifecycle, including proactive token refresh using PIN+TOTP when tokens near expiry. Provides automatic background refresh to prevent mid-session failures. **Important**: Auth lifecycle is independent of observability - stopping auth never emits heartbeat/feed/order events.
- **DhanTransport**: Wraps Tradehull API calls with retry policies, timestamp resolution, and consistent error handling; exposes normalized methods for LTP, quotes, depth, history, options, and order operations.
- **DhanMapper**: Pure functions to map Dhan-specific wire formats into ntrade domain objects (quotes, depth, order/trade books, positions, holdings).
- **Domain Models**: Order, Quote, Position, Holding, Portfolio, Account define the canonical abstractions used by engines and strategies.
- **RetryPolicy and RateLimiter**: Provide exponential backoff with jitter and token-bucket rate limiting for resilient API usage.

**Section sources**
- [dhan.py:51-75](file://ntrade/brokers/dhan.py#L51-L75)
- [dhan_auth_provider.py:28-88](file://ntrade/brokers/dhan_auth_provider.py#L28-L88)
- [dhan_transport.py:49-79](file://ntrade/brokers/dhan_transport.py#L49-L79)
- [dhan_mapper.py:35-73](file://ntrade/brokers/dhan_mapper.py#L35-L73)
- [order.py:14-41](file://ntrade/domain/orders/order.py#L14-L41)
- [quote.py:9-28](file://ntrade/domain/market/quote.py#L9-L28)
- [portfolio.py:19-61](file://ntrade/domain/portfolio.py#L19-L61)
- [retry.py:19-68](file://ntrade/execution/retry.py#L19-L68)

## Architecture Overview
The DhanBroker composes three primary subsystems with a clear delegation pattern and strict separation from observability:
- **Authentication** via DhanAuthProvider and dhan_auth.get_tradehull, which supports shared token store, JWT expiry checks, and PIN+TOTP fallback with proactive refresh. **Auth lifecycle is independent of observability**.
- **Transport** via DhanTransport, which encapsulates all Tradehull interactions with retry and normalization.
- **Mapping** via DhanMapper, which converts raw Dhan responses into domain objects.

```mermaid
sequenceDiagram
participant App as "Application"
participant Broker as "DhanBroker"
participant Auth as "DhanAuthProvider"
participant TSL as "Tradehull Instance"
participant Trans as "DhanTransport"
participant Map as "DhanMapper"
participant Obs as "Observability System"
App->>Broker : connect()
Broker->>Auth : authenticate()
Auth->>TSL : get_tradehull(env)
TSL-->>Auth : connected instance
Auth-->>Broker : tsl
Broker->>Trans : init(tsl, rate_limiter, clock)
Broker-->>App : ready
Note over Obs : No events emitted during auth lifecycle
App->>Broker : place_order(order)
Broker->>Broker : _ensure_tsl()
Broker->>Trans : place_order(**kw)
Trans->>TSL : order_placement(...)
TSL-->>Trans : order_id
Trans-->>Broker : order_id
Broker-->>App : updated Order
```

**Diagram sources**
- [dhan.py:66-75](file://ntrade/brokers/dhan.py#L66-L75)
- [dhan_auth_provider.py:47-56](file://ntrade/brokers/dhan_auth_provider.py#L47-L56)
- [dhan_auth.py:114-167](file://ntrade/brokers/dhan_auth.py#L114-L167)
- [dhan_transport.py:56-63](file://ntrade/brokers/dhan_transport.py#L56-L63)

## Detailed Component Analysis

### Authentication Flow and Credential Storage
- **Shared token store**: The system prefers a valid access token stored in a JSON file at DHAN_TOKEN_PATH. If present and not near expiry (configurable buffer), it is used directly.
- **Environment token**: If no shared token or it is near expiry, an environment-provided access token is attempted if not provably expired.
- **PIN+TOTP fallback**: When both are unavailable/expired, the system authenticates via PIN+TOTP using DHAN_PIN and DHAN_TOTP_SECRET, respecting a cooldown file at DHAN_COOLDOWN_PATH to avoid rapid re-attempts.
- **Proactive refresh**: DhanAuthProvider schedules background refresh before token expiry to prevent mid-session failures.
- **Security**: Persisted tokens and cooldown files are written with restrictive permissions (0o600).
- **Independence from Observability**: **Critical contract** - stopping authentication never emits heartbeat, feed, or order events. The auth lifecycle operates completely independently from any observability system.

```mermaid
flowchart TD
Start(["Start"]) --> CheckStore["Check shared token store"]
CheckStore --> StoreValid{"Token valid<br/>and not near expiry?"}
StoreValid --> |Yes| UseStore["Use stored token"]
StoreValid --> |No| CheckEnv["Check env access token"]
CheckEnv --> EnvValid{"Not expired?<br/>(or unparseable -> attempt)"}
EnvValid --> |Yes| UseEnv["Use env token"]
EnvValid --> |No| PinTOTP["Authenticate via PIN+TOTP"]
PinTOTP --> Cooldown{"Cooldown active?"}
Cooldown --> |Yes| ErrorCooldown["Raise cooldown error"]
Cooldown --> |No| Success{"Login ok?"}
Success --> |Yes| Persist["Persist token + cooldown"]
Success --> |No| ErrorLogin["Raise login error"]
Persist --> End(["Return Tradehull"])
UseStore --> End
UseEnv --> End
Note over Start,End: Auth lifecycle independent of observability
```

**Diagram sources**
- [dhan_auth.py:59-167](file://ntrade/brokers/dhan_auth.py#L59-L167)
- [dhan_auth_provider.py:47-74](file://ntrade/brokers/dhan_auth_provider.py#L47-L74)

**Section sources**
- [dhan_auth.py:114-167](file://ntrade/brokers/dhan_auth.py#L114-L167)
- [dhan_auth_provider.py:47-74](file://ntrade/brokers/dhan_auth_provider.py#L47-L74)
- [test_contract_auth_observability.py:1-32](file://tests/test_contract_auth_observability.py#L1-L32)

### Transport Layer Abstraction
DhanTransport wraps Tradehull calls with comprehensive error handling and retry logic:
- **RetryPolicy** for transient failures (e.g., LTP fetches with exponential backoff)
- **Consistent timestamp resolution** using injected clock or wall clock for replay parity
- **Normalization of responses** and graceful fallbacks for non-critical endpoints
- **WebSocket snapshot** for market depth with timeout protection
- **Rate limiting** integration with configurable calls per second

```mermaid
classDiagram
class DhanTransport {
-_tsl : Any
-_mapper : DhanMapper
-_retry_policy : RetryPolicy
-_rate_limiter : RateLimiter
-_clock : TradingClock
+get_ltp(symbol) float
+get_quote(symbol) Quote
+get_depth(symbol, exchange, timeout) MarketDepth?
+get_historical(symbol, exchange, timeframe, days, start, end) CandleSeries
+place_order(**kw) str
+cancel_order(order_id) void
+modify_order(order_id, **kw) void
+get_order_status(order_id) str
+get_order_detail(order_id) dict
}
class DhanMapper {
+normalize_quote(ltp, quote_data, now) Quote
+normalize_history(df) DataFrame
+filter_history(df, days, start, end) DataFrame
+normalize_orderbook(records, now) OrderBook
+normalize_tradebook(records, now) TradeBook
+positions_from_df(df) list
+holdings_from_df(df) list
+normalize_depth(symbol, bid_df, ask_df, now) MarketDepth
}
DhanTransport --> DhanMapper : "uses"
```

**Diagram sources**
- [dhan_transport.py:49-79](file://ntrade/brokers/dhan_transport.py#L49-L79)
- [dhan_mapper.py:74-136](file://ntrade/brokers/dhan_mapper.py#L74-L136)

**Section sources**
- [dhan_transport.py:49-79](file://ntrade/brokers/dhan_transport.py#L49-L79)
- [dhan_mapper.py:74-136](file://ntrade/brokers/dhan_mapper.py#L74-L136)

### Mapper Layer: Instrument Mapping, Quote Normalization, Order Status Translation
- **Instrument symbol mapping**: Converts domain instruments to Dhan tradingsymbols, especially for options where the "spaced" custom format is required.
- **Quote normalization**: Builds immutable Quote objects with optional enrichment from quote data.
- **History normalization**: Lowercases columns and filters by days/start/end.
- **Order/trade book normalization**: Maps varied field names into consistent structures.
- **Positions/holdings normalization**: Converts DataFrames into domain objects safely.
- **Depth normalization**: Builds MarketDepth from bid/ask DataFrames.

```mermaid
flowchart TD
A["Raw Dhan Response"] --> B["Normalize Columns<br/>(lowercase, keep fields)"]
B --> C{"Data Type?"}
C --> |Quote| D["Build Quote with ltp + optional fields"]
C --> |History| E["Filter by days/start/end"]
C --> |OrderBook| F["Map rows to OrderBookEntry"]
C --> |TradeBook| G["Map rows to TradeBookEntry"]
C --> |Positions| H["Map rows to Position"]
C --> |Holdings| I["Map rows to Holding"]
C --> |Depth| J["Build MarketDepth from bid/ask DFs"]
```

**Diagram sources**
- [dhan_mapper.py:74-136](file://ntrade/brokers/dhan_mapper.py#L74-L136)
- [dhan_mapper.py:139-215](file://ntrade/brokers/dhan_mapper.py#L139-L215)
- [dhan_mapper.py:218-239](file://ntrade/brokers/dhan_mapper.py#L218-L239)

**Section sources**
- [dhan_mapper.py:35-73](file://ntrade/brokers/dhan_mapper.py#L35-L73)
- [dhan_mapper.py:74-136](file://ntrade/brokers/dhan_mapper.py#L74-L136)
- [dhan_mapper.py:139-215](file://ntrade/brokers/dhan_mapper.py#L139-L215)
- [dhan_mapper.py:218-239](file://ntrade/brokers/dhan_mapper.py#L218-L239)

### Order Placement and Lifecycle
- **Supported order types**: LIMIT, MARKET, STOP_LIMIT, STOP_MARKET, COVER, BRACKET.
- **SEBI compliance**: For F&O exchanges, MARKET orders are converted to LIMIT using LTP-derived price.
- **Bracket orders**: Route through dedicated super-order endpoint with entry/target/stop legs.
- **Partial fills**: Order status includes PARTIALLY_FILLED; detail queries update filled_qty and avg_price.
- **Idempotency**: Place order returns an Order with assigned order_id; subsequent status/detail calls use this ID.

```mermaid
sequenceDiagram
participant Client as "Client"
participant Broker as "DhanBroker"
participant TSL as "Tradehull"
Client->>Broker : place_order(Order)
Broker->>Broker : _ensure_tsl()
alt F&O and MARKET
Broker->>Broker : Convert to LIMIT using LTP
end
alt BRACKET
Broker->>TSL : place_super_order(...)
TSL-->>Broker : order_id
else Standard
Broker->>TSL : order_placement(...)
TSL-->>Broker : order_id
end
Broker-->>Client : Order(status=PENDING, order_id=...)
```

**Diagram sources**
- [dhan.py:203-250](file://ntrade/brokers/dhan.py#L203-L250)
- [order.py:14-41](file://ntrade/domain/orders/order.py#L14-L41)

**Section sources**
- [dhan.py:203-250](file://ntrade/brokers/dhan.py#L203-L250)
- [order.py:14-41](file://ntrade/domain/orders/order.py#L14-L41)

### Market Data Streaming and Depth
- **LTP and quotes**: Retries on flaky endpoints; enriches with OHLC, volume, OI.
- **Historical data**: Supports intraday and daily endpoints; routes DAY requests appropriately based on instrument type/exchange.
- **Option chain**: Fetches ATM and chain dataframe; resolves real expiry dates and sets chain metadata.
- **Depth**: WebSocket snapshot bounded by timeout; returns normalized MarketDepth.

```mermaid
flowchart TD
Start(["Request Market Data"]) --> Type{"Type?"}
Type --> |LTP/Quote| Retry["RetryPolicy.execute(get_ltp)"]
Retry --> Enrich["Enrich with quote data"]
Type --> |Historical| TF["Map timeframe"]
TF --> Path{"DAY blocked?"}
Path --> |Yes| Daily["Use long-term endpoint"]
Path --> |No| Intraday["Use intraday wrapper"]
Type --> |Option Chain| Fetch["Fetch ATM + chain DF"]
Type --> |Depth| WS["WebSocket snapshot (timeout)"]
Enrich --> End(["Quote"])
Daily --> End
Intraday --> End
Fetch --> End
WS --> End
```

**Diagram sources**
- [dhan.py:114-144](file://ntrade/brokers/dhan.py#L114-L144)
- [dhan.py:158-200](file://ntrade/brokers/dhan.py#L158-L200)
- [dhan.py:135-156](file://ntrade/brokers/dhan.py#L135-L156)
- [dhan.py:121-133](file://ntrade/brokers/dhan.py#L121-L133)

**Section sources**
- [dhan.py:114-144](file://ntrade/brokers/dhan.py#L114-L144)
- [dhan.py:158-200](file://ntrade/brokers/dhan.py#L158-L200)
- [dhan.py:135-156](file://ntrade/brokers/dhan.py#L135-L156)
- [dhan.py:121-133](file://ntrade/brokers/dhan.py#L121-L133)

### Position Synchronization, Portfolio Updates, and Balance Tracking
- **Positions**: Returned as domain Position objects; raises on fetch failure to preserve state during reconciliation.
- **Holdings**: Returned as domain Holding objects; empty list on failure.
- **Balance**: Returns float; consumers guard against network blips.
- **Live P&L**: Broker-reported live P&L aggregated in Portfolio.

```mermaid
classDiagram
class Portfolio {
+positions : list[Position]
+holdings : list[Holding]
+pnl : float
+market_value : float
+live_pnl : float
+refresh() Portfolio
}
class Account {
+balance : float
+holdings : list[Holding]
+refresh() Account
}
class Position {
+symbol : string
+quantity : int
+avg_price : float
+ltp : float
+product : string
+exchange : string
+pnl : float
+market_value : float
}
class Holding {
+symbol : string
+quantity : int
+avg_price : float
+ltp : float
+pnl : float
}
Portfolio --> Position : "contains"
Portfolio --> Holding : "contains"
Account --> Holding : "contains"
```

**Diagram sources**
- [portfolio.py:63-135](file://ntrade/domain/portfolio.py#L63-L135)
- [portfolio.py:137-173](file://ntrade/domain/portfolio.py#L137-L173)
- [portfolio.py:19-61](file://ntrade/domain/portfolio.py#L19-L61)

**Section sources**
- [dhan.py:372-387](file://ntrade/brokers/dhan.py#L372-L387)
- [portfolio.py:63-135](file://ntrade/domain/portfolio.py#L63-L135)
- [portfolio.py:137-173](file://ntrade/domain/portfolio.py#L137-L173)

### Authentication Lifecycle and Observability Independence
**Updated** Enhanced documentation to clarify the critical contract that authentication lifecycle operates completely independently from observability systems.

The DhanBroker and DhanAuthProvider have no event bus and publish no observability events whatsoever. When `broker.stop()` is called, it only cancels the auth provider's token-refresh timer without triggering any HeartbeatEvent, FeedDisconnectedEvent, or OrderTimeoutEvent. This separation ensures that authentication state changes cannot affect system monitoring and alerting.

Key aspects of this contract:
- **No Event Bus**: DhanBroker has no `bus` attribute and no `publish` method
- **Timer-only Shutdown**: `stop()` method only cancels `_refresh_timer` in DhanAuthProvider
- **Clean Separation**: Auth lifecycle changes are invisible to observability systems
- **Tested Contract**: Explicitly verified in `test_contract_auth_observability.py`

```mermaid
stateDiagram-v2
[*] --> Connected
Connected --> Stopping : broker.stop()
Stopping --> [*] : Timer cancelled
note right of Stopping : No events emitted\nNo heartbeat/feed/order notifications\nOnly internal timer cleanup
```

**Diagram sources**
- [dhan.py:97-102](file://ntrade/brokers/dhan.py#L97-L102)
- [dhan_auth_provider.py:76-79](file://ntrade/brokers/dhan_auth_provider.py#L76-L79)
- [test_contract_auth_observability.py:15-32](file://tests/test_contract_auth_observability.py#L15-L32)

**Section sources**
- [dhan.py:97-102](file://ntrade/brokers/dhan.py#L97-L102)
- [dhan_auth_provider.py:76-79](file://ntrade/brokers/dhan_auth_provider.py#L76-L79)
- [test_contract_auth_observability.py:1-32](file://tests/test_contract_auth_observability.py#L1-L32)

## Dependency Analysis
- **DhanBroker** depends on DhanAuthProvider for authentication, DhanTransport for API calls, and DhanMapper for normalization.
- **DhanTransport** uses RetryPolicy for resilient execution and DhanMapper for normalization.
- **Domain models** are independent of broker specifics; adapters normalize broker responses into these models.
- **Observability Independence**: DhanBroker has no dependencies on event buses or observability systems.

```mermaid
graph TB
DhanBroker["DhanBroker"] --> DhanAuthProvider["DhanAuthProvider"]
DhanBroker --> DhanTransport["DhanTransport"]
DhanBroker --> DhanMapper["DhanMapper"]
DhanTransport --> RetryPolicy["RetryPolicy"]
DhanTransport --> DhanMapper
DhanBroker --> OrderModel["Order Model"]
DhanBroker --> QuoteModel["Quote Model"]
DhanBroker --> PortfolioModel["Portfolio/Account Models"]
DhanBroker -.-> NoObs["No Observability Dependencies"]
```

**Diagram sources**
- [dhan.py:51-75](file://ntrade/brokers/dhan.py#L51-L75)
- [dhan_transport.py:49-79](file://ntrade/brokers/dhan_transport.py#L49-L79)
- [retry.py:19-68](file://ntrade/execution/retry.py#L19-L68)
- [order.py:14-41](file://ntrade/domain/orders/order.py#L14-L41)
- [quote.py:9-28](file://ntrade/domain/market/quote.py#L9-L28)
- [portfolio.py:63-135](file://ntrade/domain/portfolio.py#L63-L135)

**Section sources**
- [dhan.py:51-75](file://ntrade/brokers/dhan.py#L51-L75)
- [dhan_transport.py:49-79](file://ntrade/brokers/dhan_transport.py#L49-L79)
- [retry.py:19-68](file://ntrade/execution/retry.py#L19-L68)

## Performance Considerations
- **RetryPolicy** with exponential backoff and jitter reduces contention and improves resilience under transient failures.
- **WebSocket depth snapshots** are timeout-bounded to avoid blocking indefinitely.
- **Timeframe mapping** enforces supported intervals to prevent silent misrouting.
- **Token proactive refresh** avoids mid-session expiry and costly re-authentication during critical operations.
- **Rate limiting** ensures compliance with Dhan API constraints (10 calls per second default).
- **Independent Lifecycle**: Auth lifecycle operations have zero overhead on observability systems, preventing unnecessary event processing.

## Troubleshooting Guide
- **Authentication failures**:
  - Ensure DHAN_CLIENT_ID is set.
  - Validate DHAN_ACCESS_TOKEN or configure DHAN_PIN/DHAN_TOTP_SECRET.
  - Check cooldown file for TOTP attempts.
- **LTP/Quote failures**:
  - Retry logic may raise BrokerDataError; inspect network and rate limits.
- **Historical data issues**:
  - Unsupported timeframes raise ValueError; verify interval strings.
  - DAY requests for FUT-type contracts route to daily endpoint automatically.
- **Order placement errors**:
  - F&O MARKET orders require LTP; ensure instrument has refreshed quote.
  - Bracket orders use super-order endpoint; check target/stop prices.
- **Connection check utility**:
  - Use check_connection.py to validate Dhan connectivity and basic market data retrieval.
- **Authentication shutdown behavior**:
  - **Expected**: `broker.stop()` only cancels the token-refresh timer
  - **Not Expected**: No heartbeat, feed, or order events should be emitted
  - **Verification**: Check that broker has no `bus` or `publish` attributes
  - **Debug**: Verify `_auth._refresh_timer` is None after stop()

**Section sources**
- [dhan_auth.py:114-167](file://ntrade/brokers/dhan_auth.py#L114-L167)
- [dhan_transport.py:87-110](file://ntrade/brokers/dhan_transport.py#L87-L110)
- [dhan.py:135-156](file://ntrade/brokers/dhan.py#L135-L156)
- [dhan.py:203-250](file://ntrade/brokers/dhan.py#L203-L250)
- [check_connection.py:17-38](file://check_connection.py#L17-L38)
- [test_contract_auth_observability.py:15-32](file://tests/test_contract_auth_observability.py#L15-L32)

## Conclusion
The DhanBroker implementation provides a robust, layered integration with Dhan's trading platform through a clean transport layer abstraction. It separates authentication, transport, and mapping concerns while enforcing domain model purity. The refactored architecture delegates all data calls through DhanTransport while maintaining the same public API surface, improving maintainability and testability. 

**Critical architectural principle**: The authentication lifecycle operates completely independently from observability systems. The DhanBroker and DhanAuthProvider have no event bus and publish no observability events, ensuring that authentication state changes (including shutdown) have no impact on system monitoring and alerting. This separation is enforced by design and tested explicitly.

Resilience patterns like retry policies, proactive token refresh, and timeout-bounded WebSocket reads ensure reliable operation under Dhan's API constraints. The design supports comprehensive order types, partial fill handling, and accurate portfolio synchronization while maintaining clean separation between connection management and system observability.

## Appendices

### Usage Examples
- **Connect to Dhan**:
  - Instantiate DhanBroker with environment path; connect() establishes authentication and transport.
- **Place orders**:
  - Use OrderFacade methods (buy/sell/limit/market/stop/cover/bracket) bound to an Instrument; underlying broker.place_order handles conversion and submission.
- **Subscribe to market data**:
  - Use subscribe/unsubscribe on BrokerAdapter to register instruments; ticks flow through instrument streams.
- **Handle authentication failures**:
  - Inspect environment variables and cooldown files; ensure PIN+TOTP credentials are correct.
- **Shutdown behavior**:
  - Call `broker.stop()` to cancel token-refresh timers; expect no observability events to be emitted.

**Section sources**
- [dhan.py:51-75](file://ntrade/brokers/dhan.py#L51-L75)
- [order.py:118-173](file://ntrade/domain/orders/order.py#L118-L173)
- [base.py:152-163](file://ntrade/brokers/base.py#L152-L163)
- [test_dhan_broker.py:22-48](file://tests/test_dhan_broker.py#L22-L48)
- [test_dhan_auth_unit.py:88-138](file://tests/test_dhan_auth_unit.py#L88-L138)
- [check_connection.py:17-38](file://check_connection.py#L17-L38)
- [test_contract_auth_observability.py:15-32](file://tests/test_contract_auth_observability.py#L15-L32)