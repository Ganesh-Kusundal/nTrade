# Broker Abstraction Pattern

<cite>
**Referenced Files in This Document**
- [base.py](file://ntrade/brokers/base.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [paper.py](file://ntrade/brokers/paper.py)
- [dhan_auth.py](file://ntrade/brokers/dhan_auth.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [dhan_mapper.py](file://ntrade/brokers/dhan_mapper.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [factories.py](file://ntrade/factories.py)
- [test_brokers.py](file://tests/test_brokers.py)
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
This document explains nTrade’s broker abstraction pattern that enables seamless switching between live and simulated trading environments without changing strategy or engine code. The core is the BrokerAdapter base class defining a uniform contract for market data, orders, portfolio, and streaming. DhanBroker implements full production capabilities over Dhan-Tradehull, while PaperBroker provides deterministic simulation for testing and backtesting. A capability system exposes broker-specific features dynamically with fail-fast behavior when unsupported.

## Project Structure
The broker layer lives under ntrade/brokers and is composed of:
- Base adapter and capability registry
- Live broker implementation (Dhan)
- Simulation broker (Paper)
- Authentication, transport, and mapping utilities for Dhan

```mermaid
graph TB
subgraph "Brokers"
BASE["BrokerAdapter<br/>(base.py)"]
DHAN["DhanBroker<br/>(dhan.py)"]
PAPER["PaperBroker<br/>(paper.py)"]
end
subgraph "Dhan Providers"
AUTH["DhanAuthProvider<br/>(dhan_auth_provider.py)"]
TRANS["DhanTransport<br/>(dhan_transport.py)"]
MAP["DhanMapper<br/>(dhan_mapper.py)"]
AUTHMOD["dhan_auth module<br/>(dhan_auth.py)"]
end
CAPS["Capability System<br/>(capabilities.py)"]
BASE --> DHAN
BASE --> PAPER
DHAN --> AUTH
DHAN --> TRANS
DHAN --> MAP
TRANS --> AUTHMOD
CAPS --> DHAN
```

**Diagram sources**
- [base.py](file://ntrade/brokers/base.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [paper.py](file://ntrade/brokers/paper.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [dhan_mapper.py](file://ntrade/brokers/dhan_mapper.py)
- [dhan_auth.py](file://ntrade/brokers/dhan_auth.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)

**Section sources**
- [base.py](file://ntrade/brokers/base.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [paper.py](file://ntrade/brokers/paper.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)

## Core Components
- **BrokerAdapter**: Abstract base defining connect/disconnect, market data (quote, depth, historical), order lifecycle, portfolio queries, and subscription multiplexing. It also injects a clock for zero-parity timestamps across replay/live.
- **DhanBroker**: Full-featured production broker implementing all adapter methods, integrating authentication, transport, and normalization layers. Routes all REST calls through the shared `BrokerRateGate` (constructed in `connect()`) and exposes broker-specific features via the capability system.
- **PaperBroker**: Deterministic in-memory broker for tests/backtests with realistic quote/depth/history generation and immediate fills.
- **Capability System**: Dynamic feature discovery via decorators; `instrument.broker.<capability>()` resolves at runtime based on the active broker.
- **BrokerRateGate / Quota / RateLimited**: Multi-window, multi-class sliding-window rate gate — the single choke point for all outbound broker REST calls. Four quota classes (QUOTE, DATA, ORDER, NON_TRADING) with configurable windows; DH-904 backs a class off via `penalize()`.
- **RetryPolicy**: Exponential-backoff retry that never retries `RateLimited` exceptions (DH-904 surfaces immediately); transient LTP failures retry.

Key responsibilities:
- Uniform domain-facing API regardless of broker
- Time parity through injected clock
- Fail-fast capability checks to avoid hidden if/else branches
- Clean separation of concerns: auth, transport, mapping
- Single rate-gate choke point preventing quota exhaustion across concurrent calls

**Section sources**
- [base.py](file://ntrade/brokers/base.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [paper.py](file://ntrade/brokers/paper.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)

## Architecture Overview
The DhanBroker composes three providers behind the BrokerAdapter contract:
- **DhanAuthProvider** — Lifecycle management and proactive token refresh using PIN+TOTP fallback. Produces the authenticated Tradehull instance (`tsl`) shared by the broker and the market feed.
- **DhanTransport** — All REST calls routed through a per-method choke point: every `self._tsl.*` call passes through `_invoke(quota, fn)` which acquires the session's `BrokerRateGate` before firing and normalises DH-904 / Rate_Limit failures into the typed `RateLimited` exception. The transport holds the shared `BrokerRateGate` and an injectable clock for zero-parity timestamps.
- **DhanMapper** — Pure data normalization functions (wire→domain).

The `BrokerRateGate` is the **single choke point** every outbound broker REST
call passes through — it is constructed once per session in
`DhanBroker.connect()` and shared with `DhanTransport`, `BrokerExecution`
and the live feed. It is a multi-window, multi-class sliding-window gate
(real `deque` timestamps, stdlib-only) with four quota classes matching
Dhan's documented rate-limit table:

| Quota class | Windows |
|---|---|
| `QUOTE` | 1/s |
| `DATA` | 5/s, 100 000/day |
| `ORDER` | 10/s, 250/min, 1000/h, 7000/day |
| `NON_TRADING` | 20/s |

`_invoke` acquires the gate, runs the function, and on a DH-904 backs the
class off via `gate.penalize(quota, retry_after)` so the next acquire waits
before re-firing. `status()` exposes read-only telemetry (windows, cooldown
remaining, blocked flag) consumed by the pre-deploy quota-headroom report.
`clock` and `sleep` are injectable, making rate-gate behaviour deterministic
in tests.

```mermaid
classDiagram
class BrokerAdapter {
+name : string
+connect() BrokerAdapter
+disconnect() void
+get_quote(instrument) Quote
+get_depth(instrument) MarketDepth|None
+get_historical(instrument, timeframe, days, start, end) CandleSeries
+place_order(order) Order
+cancel_order(order) Order
+modify_order(order, ...) Order
+get_order_status(order) Order
+get_balance() float
+get_positions() list
+get_holdings() list
+subscribe(instrument) void
+unsubscribe(instrument) void
}
class DhanBroker {
+name = "dhan"
+connect() DhanBroker
+set_clock(clock) DhanBroker
+get_quote(...) Quote
+get_depth(...) MarketDepth|None
+get_historical(...) CandleSeries
+place_order(...) Order
+cancel_order(...) Order
+modify_order(...) Order
+get_order_status(...) Order
+get_balance() float
+get_positions() list
+get_holdings() list
}
class PaperBroker {
+name = "paper"
+seed_quote(symbol, ltp, **kw) Quote
+seed_history(symbol, rows, timeframe, start_price) DataFrame
+get_quote(...) Quote
+get_depth(...) MarketDepth
+get_historical(...) CandleSeries
+place_order(...) Order
+cancel_order(...) Order
+modify_order(...) Order
+get_order_status(...) Order
+get_balance() float
+get_positions() list
+get_holdings() list
}
class DhanAuthProvider {
+authenticate(gate) Any
+refresh_if_needed() Any
+stop() void
+tsl Any
+is_authenticated bool
+time_until_expiry() float
}
class DhanTransport {
+_gate BrokerRateGate
+_clock TradingClock
+_retry_policy RetryPolicy
+_invoke(quota, fn, *, retryable) Any
+get_ltp(symbol) float
+get_quote(symbol) Quote
+get_depth(symbol, exchange, timeout) MarketDepth|None
+get_historical(...) CandleSeries
+get_long_term_historical(...) DataFrame
+get_option_chain(...)
+get_expiry_list(...) list[date]
+get_expiry_date(...) list[date]
+get_future_script(...) str|None
+get_lot_size(symbol) int
+get_ohlc(symbol) dict
+place_order(**kw) str
+place_super_order(**kw) str
+cancel_order(order_id) void
+modify_order(order_id, **kw) void
+get_order_status(order_id) str
+get_order_detail(order_id) dict
+get_executed_price(order_id) float
+get_executed_price_and_time(order_id) tuple
+get_orderbook() list[dict]
+get_trade_book() list[dict]
+order_report() dict
+get_live_pnl() float
+get_balance() float
+get_positions() list
+get_holdings() list
+instrument_df DataFrame|None
}
class DhanMapper {
<<static>>
+to_trading_symbol(instrument) str
+map_timeframe(tf) str
+normalize_quote(ltp, quote_data, now) Quote
+normalize_history(df) DataFrame
+filter_history(df, days, start, end) DataFrame
+normalize_orderbook(records, now) OrderBook
+normalize_tradebook(records, now) TradeBook
+positions_from_df(df) list
+holdings_from_df(df) list
+normalize_depth(symbol, bid_df, ask_df, now) MarketDepth
}
class BrokerRateGate {
+acquire(quota) void
+penalize(quota, seconds) void
+status() dict
}
class Quota {
<<enumeration>>
QUOTE DATA ORDER NON_TRADING
}
class RateLimited {
+quota Quota
+retry_after float|None
}
BrokerAdapter <|-- DhanBroker
BrokerAdapter <|-- PaperBroker
DhanBroker --> DhanAuthProvider : "composes"
DhanBroker --> DhanTransport : "uses"
DhanBroker --> DhanMapper : "normalizes"
DhanBroker --> BrokerRateGate : "owns/shared gate"
DhanTransport --> BrokerRateGate : "acquires"
DhanTransport --> DhanMapper : "uses"
BrokerRateGate --> Quota
BrokerRateGate --> RateLimited
```

**Diagram sources**
- [base.py](file://ntrade/brokers/base.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [paper.py](file://ntrade/brokers/paper.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [dhan_mapper.py](file://ntrade/brokers/dhan_mapper.py)
- [rate_limit.py](file://ntrade/execution/rate_limit.py)
- [retry.py](file://ntrade/execution/retry.py)

## Detailed Component Analysis

### BrokerAdapter (Base Contract)
- Defines the minimal interface every broker must implement: connection, quotes, depth, history, orders, portfolio, and subscriptions.
- Provides default implementations for optional operations (e.g., get_depth returns None by default).
- Manages subscription state and dispatches ticks to instruments’ streams.
- Clock injection ensures replay parity.

```mermaid
flowchart TD
Start(["BrokerAdapter usage"]) --> Connect["connect()"]
Connect --> Data["get_quote / get_depth / get_historical"]
Data --> Orders["place_order / cancel_order / modify_order"]
Orders --> Portfolio["get_balance / get_positions / get_holdings"]
Portfolio --> Stream["subscribe / unsubscribe / _dispatch_tick"]
Stream --> End(["Consistent domain objects"])
```

**Section sources**
- [base.py](file://ntrade/brokers/base.py)

### DhanBroker (Production Implementation)
- Implements all adapter methods with robust retry and error handling.
- Enforces SEBI-compliant LIMIT orders for F&O exchanges by converting MARKET to LIMIT with LTP-based price offsets.
- Supports bracket orders via place_super_order.
- Normalizes quotes, depth, history, option chains, and order books into domain types.
- Integrates with DhanAuthProvider for token lifecycle and DhanTransport for API calls.
- Routes every outbound REST call through the shared `BrokerRateGate` via `DhanTransport._invoke(Quota, fn)` with four quota classes: `Quota.QUOTE` (1/s), `Quota.DATA` (5/s, 100k/day), `Quota.ORDER` (10/s, 250/min, 1000/h, 7000/day), `Quota.NON_TRADING` (20/s).
- Dhan-specific capabilities (depth20, kill_switch, expiry_list, lot_size, etc.) wrap their broker calls in `broker._gated(<class>, lambda: broker.tsl.<method>(...))` so they pay the correct quota class and DH-904 surfaces as `RateLimited`.

```mermaid
sequenceDiagram
participant Strat as "Strategy/Engine"
participant Broker as "DhanBroker"
participant Auth as "DhanAuthProvider"
participant Trans as "DhanTransport"
participant Gate as "BrokerRateGate"
participant TSL as "Tradehull"
Strat->>Broker : place_order(Order)
Broker->>Auth : refresh_if_needed()
Auth-->>Broker : tsl (or refreshed)
Broker->>Trans : _invoke(Quota.ORDER, place_order(...))
Trans->>Gate : acquire(ORDER)
Gate-->>Trans : slot granted
Trans->>TSL : order_placement(...)
TSL-->>Trans : order_id
Trans-->>Broker : order_id
Broker-->>Strat : Order(PENDING)
```

**Diagram sources**
- [dhan.py](file://ntrade/brokers/dhan.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)

**Section sources**
- [dhan.py](file://ntrade/brokers/dhan.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [rate_limit.py](file://ntrade/execution/rate_limit.py)
- [retry.py](file://ntrade/execution/retry.py)

### PaperBroker (Simulation)
- Always connected, deterministic seeding for quotes and history.
- Immediate fills for orders with realistic prices from seeded quotes.
- In-memory order book and trade book for inspection.
- Pushes synthetic ticks to instrument streams for event-driven testing.

```mermaid
flowchart TD
Seed["seed_quote / seed_history"] --> Quote["get_quote()"]
Quote --> Depth["get_depth()"]
Depth --> History["get_historical()"]
History --> Place["place_order()"]
Place --> Fill["Immediate fill with LTP or limit price"]
Fill --> Book["OrderBook / TradeBook updated"]
```

**Diagram sources**
- [paper.py](file://ntrade/brokers/paper.py)

**Section sources**
- [paper.py](file://ntrade/brokers/paper.py)

### Capability System (Dynamic Feature Discovery)
- Capabilities are registered globally with a name, function, and allowed brokers.
- instrument.broker.<cap>() invokes the capability only if supported; otherwise raises AttributeError (fail-fast).
- Many Dhan-specific features are exposed this way (e.g., depth20, market_feed, kill_switch, margin_calculator, super orders).

```mermaid
sequenceDiagram
participant Inst as "Instrument"
participant Facade as "BrokerExtensionFacade"
participant Reg as "Capability Registry"
participant Fn as "Capability Function"
Inst->>Facade : __getattr__("depth20")
Facade->>Reg : lookup("depth20")
Reg-->>Facade : Capability(brokers=("dhan",))
Facade->>Inst : resolve broker_adapter.name
alt supports broker
Facade->>Fn : invoke(instrument, args)
Fn-->>Facade : result
Facade-->>Inst : result
else not supported
Facade-->>Inst : raise AttributeError
end
```

**Diagram sources**
- [capabilities.py](file://ntrade/brokers/capabilities.py)
- [dhan.py](file://ntrade/brokers/dhan.py)

**Section sources**
- [capabilities.py](file://ntrade/brokers/capabilities.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [test_brokers.py](file://tests/test_brokers.py)

### Authentication Flow (Dhan)
- Prioritizes shared token store, then env access token (with expiry buffer), finally PIN+TOTP fallback.
- Proactive background refresh avoids mid-session expiry.
- Cooldown protection prevents rapid TOTP attempts.

```mermaid
sequenceDiagram
participant App as "DhanBroker"
participant Provider as "DhanAuthProvider"
participant AuthMod as "dhan_auth.get_tradehull"
participant Store as "Shared Token Store"
participant TSL as "Tradehull"
App->>Provider : authenticate(gate)
Provider->>AuthMod : get_tradehull(env_path, env)
AuthMod->>Store : read token (if exists)
alt valid token
AuthMod-->>Provider : TSL(access_token)
else expired or missing
AuthMod->>AuthMod : try PIN+TOTP
AuthMod-->>Provider : TSL(pin_totp)
AuthMod->>Store : persist token + cooldown
end
Provider-->>App : TSL (with scheduled refresh)
```

**Diagram sources**
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [dhan_auth.py](file://ntrade/brokers/dhan_auth.py)

**Section sources**
- [dhan_auth.py](file://ntrade/brokers/dhan_auth.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)

### Connection Management and Streaming
- BrokerAdapter tracks subscriptions per instrument symbol and notifies streams on disconnect.
- DhanBroker constructs a per-session `BrokerRateGate` in `connect()` and shares it with `DhanTransport`, `BrokerExecution`, and the live feed — all outbound REST calls are throttled through it.
- DhanBroker propagates clock to transport for time parity.
- PaperBroker always connected; push_tick emits ticks to subscribed streams.

```mermaid
flowchart TD
Sub["subscribe(instrument)"] --> State["Set SubscriptionState.SUBSCRIBED"]
State --> Dispatch["_dispatch_tick -> instrument._stream.ingest_tick"]
Disconnect["disconnect()"] --> Notify["notify_disconnect() on each stream"]
Notify --> Clear["Clear subscriptions"]
```

**Section sources**
- [base.py](file://ntrade/brokers/base.py)
- [paper.py](file://ntrade/brokers/paper.py)

### Error Handling Strategies
- DhanTransport wraps flaky endpoints with retries; raises `BrokerDataError` for critical failures (e.g., zero LTP).
- DH-904 / Rate_Limit failures are normalised into the typed `RateLimited` exception at the transport boundary (`_invoke`) and are **never** retried — they propagate up so data reads fail loud instead of silently returning empty/zero (B-005, K-021 contracts). `is_rate_limited()` matches Dhan's error text variants.
- DhanBroker converts marketplace errors to clear RuntimeError messages and updates order status consistently.
- `get_balance()` / `get_positions()` raise on transport failure instead of collapsing to `0.0` / `[]`, so position sync can distinguish "flat" from "error".
- PaperBroker never fails; deterministic behavior aids debugging.

```mermaid
flowchart TD
    Call["API call"] --> Try{"Retry policy"}
    Try --> |Success| Return["Return normalized data"]
    Try --> |Failure| Rate{"RateLimited (DH-904)?"}
    Rate --> |Yes| Raise["Raise RateLimited (never retried)"]
    Rate --> |No| Critical{"Critical endpoint?"}
    Critical --> |Yes| Raise2["Raise BrokerDataError"]
    Critical --> |No| Fallback["Return empty/zero defaults"]
```

**Section sources**
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [rate_limit.py](file://ntrade/execution/rate_limit.py)
- [retry.py](file://ntrade/execution/retry.py)
- [dhan.py](file://ntrade/brokers/dhan.py)

### Broker-Specific Order Routing and Fill Handling
- DhanBroker enforces LIMIT for F&O, converts MARKET to LIMIT with LTP-based offset.
- Bracket orders routed to place_super_order; standard orders via order_placement.
- PaperBroker fills immediately at LTP or limit price; maintains internal order list.

```mermaid
flowchart TD
OType{"Order Type"} --> |MARKET & F&O| Convert["Convert to LIMIT with LTP offset"]
OType --> |BRACKET| Super["place_super_order(entry,target,stop)"]
OType --> |LIMIT/MARKET| Standard["order_placement(...)"]
Standard --> Status["Update OrderStatus.PENDING"]
Super --> Status
```

**Section sources**
- [dhan.py](file://ntrade/brokers/dhan.py)
- [paper.py](file://ntrade/brokers/paper.py)

### Market Data Normalization
- DhanMapper normalizes quotes, depth, history, and books into domain types.
- DhanBroker uses mapper helpers to ensure consistent field names and types.
- PaperBroker generates synthetic but structurally identical data.

```mermaid
classDiagram
class DhanMapper {
+normalize_quote(ltp, quote_data, now) Quote
+normalize_depth(symbol, bid_df, ask_df, now) MarketDepth
+normalize_history(df) DataFrame
+normalize_orderbook(records, now) OrderBook
+normalize_tradebook(records, now) TradeBook
}
class DhanBroker {
+get_quote(...) Quote
+get_depth(...) MarketDepth
+get_historical(...) CandleSeries
}
DhanBroker --> DhanMapper : "uses for normalization"
```

**Diagram sources**
- [dhan_mapper.py](file://ntrade/brokers/dhan_mapper.py)
- [dhan.py](file://ntrade/brokers/dhan.py)

**Section sources**
- [dhan_mapper.py](file://ntrade/brokers/dhan_mapper.py)
- [dhan.py](file://ntrade/brokers/dhan.py)

### Seamless Switching Between Brokers
- Strategy/engine code interacts only with BrokerAdapter methods.
- Swap DhanBroker for PaperBroker by changing instantiation; no other changes required.
- Factories propagate broker instances to instruments automatically.

```mermaid
sequenceDiagram
participant Engine as "Engine"
participant Factory as "InstrumentFactory"
participant Inst as "Instrument"
participant Broker as "BrokerAdapter"
Engine->>Factory : equity("RELIANCE", broker=Broker)
Factory-->>Engine : Instrument(broker=Broker)
Engine->>Inst : order.buy(quantity, price)
Inst->>Broker : place_order(Order)
Broker-->>Inst : Order(updated)
```

**Diagram sources**
- [factories.py](file://ntrade/factories.py)
- [base.py](file://ntrade/brokers/base.py)

**Section sources**
- [factories.py](file://ntrade/factories.py)
- [base.py](file://ntrade/brokers/base.py)

## Dependency Analysis
- DhanBroker depends on DhanAuthProvider, DhanTransport, and DhanMapper.
- DhanBroker owns the shared `BrokerRateGate`, which DhanTransport acquires for every outbound call.
- DhanTransport depends on DhanMapper, the underlying Tradehull library, and RetryPolicy.
- Capability system decouples broker-specific extensions from the base API.
- Tests validate capability availability and paper broker behavior.

```mermaid
graph LR
DhanBroker["DhanBroker"] --> AuthProv["DhanAuthProvider"]
DhanBroker --> Transport["DhanTransport"]
DhanBroker --> Mapper["DhanMapper"]
DhanBroker --> Gate["BrokerRateGate"]
Transport --> Gate
Transport --> Mapper
Transport --> Retry["RetryPolicy"]
Gate --> Quota["Quota"]
Gate --> RateLim["RateLimited"]
CapSys["Capabilities"] --> DhanBroker
Tests["Tests"] --> CapSys
Tests --> PaperBroker["PaperBroker"]
```

**Diagram sources**
- [dhan.py](file://ntrade/brokers/dhan.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [dhan_mapper.py](file://ntrade/brokers/dhan_mapper.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)
- [test_brokers.py](file://tests/test_brokers.py)

**Section sources**
- [dhan.py](file://ntrade/brokers/dhan.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)
- [test_brokers.py](file://tests/test_brokers.py)

## Performance Considerations
- Retry policies mitigate transient network issues; DH-904 (RateLimited) is never retried — retrying a throttled call only amplifies quota exhaustion.
- WebSocket depth snapshot bounded by timeout to prevent blocking.
- The `BrokerRateGate` backpressures all concurrent calls: `ParallelHistoryFetcher` uses 4 workers (tuned to the 5/s DATA quota) so the gate never exceeds the quota.
- Clock injection ensures reproducible timing in replay/backtest scenarios.
- `clock` and `sleep` are injectable on `BrokerRateGate` so rate-gate tests are deterministic.

## Troubleshooting Guide
- Authentication failures: Check shared token store, env variables, and PIN/TOTP settings; observe cooldown logs.
- Zero LTP or empty quotes: Inspect retry behavior and network connectivity; consider refreshing instrument quotes before placing orders. A DH-904 surfaces as `RateLimited` — check the pre-deploy quota-headroom report (`BrokerRateGate.status()`).
- Unsupported capabilities: Ensure the current broker supports the requested capability; use `available()` to list supported features. Capabilities route through `broker._gated(quota, fn)` so every call pays the right quota class.
- Order rejections: Verify SEBI constraints (F&O MARKET→LIMIT conversion) and LTP availability.
- Rate-limited calls: `get_balance()` / `get_positions()` raise on transport failure (not collapsing to `0.0`/`[]`); `PositionSyncEngine` keeps the previous state on failure (failure-safe).

**Section sources**
- [dhan_auth.py](file://ntrade/brokers/dhan_auth.py)
- [dhan_transport.py](file://ntrade/brokers/dhan_transport.py)
- [rate_limit.py](file://ntrade/execution/rate_limit.py)
- [retry.py](file://ntrade/execution/retry.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)

## Conclusion
nTrade's broker abstraction cleanly separates domain logic from broker specifics. BrokerAdapter defines a stable contract; DhanBroker delivers production-grade functionality with robust auth, transport, and normalization; PaperBroker offers deterministic simulation. The capability system enables dynamic, safe extension points. The `BrokerRateGate` is the single choke point for all outbound broker REST calls, with four quota classes (QUOTE, DATA, ORDER, NON_TRADING) and DH-904 surfaced as a typed `RateLimited` exception that is never retried. Together, these patterns allow seamless switching between live and test environments without code changes.

## Appendices

### Implementing a Custom Broker
Steps:
1. Extend BrokerAdapter and implement required abstract methods (connect, get_quote, get_historical, place_order).
2. Optionally override optional methods (get_depth, cancel_order, modify_order, get_balance, etc.).
3. If adding broker-specific features, register them via @capability(name, brokers=(...)).
4. Wire the broker into factories or directly instantiate it for instruments.

Example references:
- Base contract and defaults: [base.py](file://ntrade/brokers/base.py)
- Registration decorator and facade: [capabilities.py](file://ntrade/brokers/capabilities.py)
- Production implementation patterns: [dhan.py](file://ntrade/brokers/dhan.py)
- Simulation reference: [paper.py](file://ntrade/brokers/paper.py)

**Section sources**
- [base.py](file://ntrade/brokers/base.py)
- [capabilities.py](file://ntrade/brokers/capabilities.py)
- [dhan.py](file://ntrade/brokers/dhan.py)
- [paper.py](file://ntrade/brokers/paper.py)