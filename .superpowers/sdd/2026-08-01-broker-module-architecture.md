# Broker Module Architecture — Class Diagram & Interface Guide

**Module:** `ntrade/brokers/`  
**Purpose:** Broker-agnostic interface with Dhan/Paper implementations and capability-driven extensions

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         PUBLIC API                               │
│  (imported by ntrade/__init__.py and ntrade/brokers/__init__.py)│
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  BrokerAdapter (ABC)          ← Abstract interface              │
│    ├── connect() / disconnect()                                  │
│    ├── get_quote() / get_depth() / get_historical()             │
│    ├── place_order() / cancel_order() / modify_order()          │
│    ├── get_balance() / get_positions() / get_holdings()         │
│    └── subscribe() / unsubscribe()                               │
└─────────────────────────────────────────────────────────────────┘
           ↑                    ↑
           │                    │
    ┌──────┴──────┐      ┌─────┴──────┐
    │ DhanBroker  │      │PaperBroker │
    │  (live)     │      │  (offline) │
    └──────┬──────┘      └────────────┘
           │
           │ composes
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  DhanBroker Internal Components (Provider Pattern)              │
├─────────────────────────────────────────────────────────────────┤
│  DhanAuthProvider    → Authentication lifecycle + auto-refresh  │
│  DhanTransport       → Tradehull API calls + retry              │
│  DhanMapper          → Pure data normalization (static)         │
│  DhanAuth (module)   → Credential resolution + token cache      │
└─────────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  Capability System (Broker-Specific Extensions)                 │
├─────────────────────────────────────────────────────────────────┤
│  @capability("depth20", brokers=("dhan",))                      │
│  @capability("kill_switch", brokers=("dhan",))                  │
│  @capability("margin_calculator", brokers=("dhan",))            │
│  ... 31 capabilities registered dynamically                     │
│                                                                 │
│  instrument.broker.<capability>() → dynamic dispatch            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Class Diagram — Full Detail

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           <<abstract>>                                   │
│                          BrokerAdapter                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  name: str = "base"                                                      │
│  _subscriptions: dict[str, Instrument]                                   │
│  _connected: bool                                                        │
│  _clock: TradingClock | None                                             │
├─────────────────────────────────────────────────────────────────────────┤
│  «constructor» __init__(clock=None)                                      │
├─────────────────────────────────────────────────────────────────────────┤
│  «abstract» connect() → BrokerAdapter                                   │
│  disconnect() → None                                                     │
│  connected: bool «property»                                              │
│  set_clock(clock) → BrokerAdapter                                        │
│  _ts(now=None) → datetime                                                │
├─────────────────────────────────────────────────────────────────────────┤
│  «abstract» get_quote(instrument) → Quote                               │
│  get_depth(instrument) → MarketDepth | None                              │
│  «abstract» get_historical(instrument, timeframe, days, start, end)     │
│                → CandleSeries                                            │
│  get_option_chain(underlying, expiry, num_strikes) → OptionChain        │
├─────────────────────────────────────────────────────────────────────────┤
│  «abstract» place_order(order) → Order                                  │
│  cancel_order(order) → Order                                             │
│  modify_order(order, price, quantity, order_type, trigger_price) → Order│
│  get_order_status(order) → Order                                         │
│  get_order_detail(order_id) → dict                                       │
│  get_executed_price(order) → float                                       │
│  get_executed_price_and_time(order) → (float, str)                      │
│  get_orderbook() → OrderBook                                             │
│  get_trade_book() → TradeBook                                            │
│  order_report() → dict                                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  get_balance() → float                                                   │
│  get_positions() → list[Position]                                        │
│  get_holdings() → list[Holding]                                          │
│  get_live_pnl() → float                                                  │
├─────────────────────────────────────────────────────────────────────────┤
│  subscribe(instrument) → None                                            │
│  unsubscribe(instrument) → None                                          │
│  _dispatch_tick(instrument, tick) → None                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    △
                                    │ inherits
                    ┌───────────────┴───────────────┐
                    │                               │
        ┌───────────┴──────────┐       ┌────────────┴──────────┐
        │      DhanBroker      │       │      PaperBroker      │
        │   (live trading)     │       │   (offline/testing)   │
        ├──────────────────────┤       ├───────────────────────┤
        │ name = "dhan"        │       │ name = "paper"        │
        │ env_path: str        │       │ _quotes: dict         │
        │ env: dict | None     │       │ _orders: dict         │
        │ tsl: Tradehull       │       │ _positions: dict      │
        │ _auth: DhanAuthProvider│     │ _balance: float       │
        │ _transport: DhanTransport│   │ _clock: TradingClock  │
        │ _mapper: DhanMapper  │       │                       │
        ├──────────────────────┤       ├───────────────────────┤
        │ __init__(env_path,   │       │ __init__(balance,     │
        │   env, connect,clock)│       │   clock)              │
        │ connect()            │       │ connect()             │
        │ _ensure_tsl()        │       │ get_quote()           │
        │ get_quote()          │       │ get_historical()      │
        │ get_depth()          │       │ place_order()         │
        │ get_historical()     │       │ cancel_order()        │
        │ get_option_chain()   │       │ get_balance()         │
        │ place_order()        │       │ get_positions()       │
        │ cancel_order()       │       │ get_holdings()        │
        │ modify_order()       │       │ subscribe()           │
        │ get_order_status()   │       └───────────────────────┘
        │ get_orderbook()      │
        │ get_trade_book()     │
        │ get_balance()        │
        │ get_positions()      │
        │ get_holdings()       │
        │ ... 30+ methods      │
        └───────────┬──────────┘
                    │ composes (4 providers)
                    │
    ┌───────────────┼───────────────┬────────────────┐
    │               │               │                │
    ▼               ▼               ▼                ▼
┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────┐
│DhanAuth    │ │DhanTransport│ │DhanMapper │ │DhanAuth       │
│Provider    │ │             │ │  (static) │ │(module funcs) │
├────────────┤ ├────────────┤ ├────────────┤ ├────────────────┤
│_env_path   │ │_tsl: Any   │ │«static»   │ │load_env()     │
│_env: dict  │ │_mapper     │ │to_trading_│ │jwt_expiry()   │
│_tsl: Any   │ │_retry_policy││  symbol() │ │get_tradehull()│
│_refresh_   │ │_clock      │ │normalize_ │ │_token_from_   │
│  timer     │ │            │ │  quote()  │ │  shared_store()│
│_lock       │ │            │ │normalize_ │ │_persist_shared│
├────────────┤ │            │ │  depth()  │ │_cooldown_     │
│authenticate│ │            │ │positions_ │ │  active()     │
│refresh_if_ │ │            │ │  from_df()│ │EXPIRY_BUFFER_S│
│  needed()  │ │            │ │holdings_  │ │TOTP_COOLDOWN_S│
│stop()      │ │            │ │  from_df()│ │               │
│time_until_ │ │            │ │           │ │               │
│  expiry()  │ │            │ │           │ │               │
├────────────┤ │            │ │           │ │               │
│«property»  │ │            │ │           │ │               │
│  tsl       │ │            │ │           │ │               │
│is_authent- │ │            │ │           │ │               │
│  icated    │ │            │ │           │ │               │
└────────────┘ └────────────┘ └────────────┘ └────────────────┘
```

---

## 3. Capability System — Dynamic Extension Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                    Capability Registry                          │
│                  (_CAPABILITIES: dict)                          │
├─────────────────────────────────────────────────────────────────┤
│  "depth20"            → Capability(fn, brokers=("dhan",))      │
│  "kill_switch"        → Capability(fn, brokers=("dhan",))      │
│  "margin_calculator"  → Capability(fn, brokers=("dhan",))      │
│  "place_super_order"  → Capability(fn, brokers=("dhan",))      │
│  "place_slice_order"  → Capability(fn, brokers=("dhan",))      │
│  "place_forever_order"→ Capability(fn, brokers=("dhan",))      │
│  "expiry_list"        → Capability(fn, brokers=("dhan",))      │
│  "lot_size"           → Capability(fn, brokers=("dhan",))      │
│  "future_script"      → Capability(fn, brokers=("dhan",))      │
│  "atm_strike"         → Capability(fn, brokers=("dhan",))      │
│  "itm_strike"         → Capability(fn, brokers=("dhan",))      │
│  "otm_strike"         → Capability(fn, brokers=("dhan",))      │
│  "ohlc"               → Capability(fn, brokers=("dhan",))      │
│  "start_date"         → Capability(fn, brokers=("dhan",))      │
│  "instrument_file"    → Capability(fn, brokers=("dhan",))      │
│  "long_term_history"  → Capability(fn, brokers=("dhan",))      │
│  "enable_pnl_exit"    → Capability(fn, brokers=("dhan",))      │
│  "market_feed"        → Capability(fn, brokers=("dhan",))      │
│  "order_update_stream"→ Capability(fn, brokers=("dhan",))      │
│  ... (31 total)                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↑
                              │ registered via
                              │
┌─────────────────────────────────────────────────────────────────┐
│  @capability("depth20", brokers=("dhan",))                      │
│  def _depth20(instrument, levels=20):                           │
│      broker = instrument.broker_adapter                         │
│      return broker.get_depth(instrument, levels=levels)         │
└─────────────────────────────────────────────────────────────────┘
                              ↑
                              │ invoked via
                              │
┌─────────────────────────────────────────────────────────────────┐
│  instrument.broker.depth20(levels=20)                           │
│       ↓                                                         │
│  BrokerExtensionFacade.__getattr__("depth20")                   │
│       ↓                                                         │
│  Capability.invoke(instrument, levels=20)                       │
│       ↓                                                         │
│  _depth20(instrument, levels=20)                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Interface Exposure — How Users Access the Broker

### 4.1 Direct Broker Access (Low-Level)

```python
from ntrade.brokers import DhanBroker

# Construct broker directly
broker = DhanBroker(env_path=".env")

# Market data
quote = broker.get_quote(instrument)
depth = broker.get_depth(instrument)
history = broker.get_historical(instrument, timeframe="5m", days=5)

# Orders
order = broker.place_order(order)
broker.cancel_order(order)
broker.modify_order(order, price=100.0)

# Portfolio
balance = broker.get_balance()
positions = broker.get_positions()
holdings = broker.get_holdings()
```

### 4.2 Via Market Facade (High-Level — Recommended)

```python
from ntrade import Market

m = Market(broker="dhan")

# Instruments auto-wire the broker
nifty = m.index("NIFTY")
tcs = m.equity("TCS")

# Market data via instrument
quote = nifty.quote()          # → Quote
history = nifty.history("5m")  # → CandleSeries
depth = nifty.depth()          # → MarketDepth | None

# Orders via fluent API
order = tcs.order.buy(quantity=10, order_type="LIMIT", price=3500.0)
tcs.order.cancel(order)

# Portfolio
balance = m.account().balance()
positions = m.portfolio().positions()
```

### 4.3 Via Capability System (Broker-Specific Extensions)

```python
from ntrade import Market

m = Market(broker="dhan")
nifty = m.index("NIFTY")

# Capabilities are accessed via instrument.broker facade
# They dispatch dynamically based on broker type

# Dhan-specific capabilities
chain = nifty.broker.option_chain(expiry=0, num_strikes=10)
atm = nifty.broker.atm_strike(expiry=0)
lot = nifty.broker.lot_size()
expiry_list = nifty.broker.expiry_list()

# Advanced orders
nifty.broker.place_super_order(
    side="BUY", quantity=50, order_type="LIMIT",
    price=24400, target_price=24500, stop_loss_price=24350
)

# Risk management
nifty.broker.kill_switch(action="DEACTIVATE")
nifty.broker.margin_calculator(
    quantity=50, transaction_type="BUY",
    trade_type="MIS", price=24400
)

# Check available capabilities
available = nifty.broker.available()
# → ["depth20", "kill_switch", "margin_calculator", ...]
```

---

## 5. Public API Surface — What's Exported

### 5.1 From `ntrade.brokers` Package

```python
__all__ = [
    "BrokerAdapter",           # Abstract base class
    "BrokerExtensionFacade",   # Capability facade
    "capability",              # Decorator for registering capabilities
    "PaperBroker",             # Offline broker for testing
]

# DhanBroker is NOT exported (lazy import to avoid pulling Dhan-Tradehull)
# Access via: from ntrade.brokers.dhan import DhanBroker
```

### 5.2 From `ntrade` Root Package

```python
# ntrade/__init__.py
from ntrade.facade import Market
from ntrade.registry import BrokerRegistry, SymbolMaster

__all__ = ["Market", "BrokerRegistry", "SymbolMaster"]
```

### 5.3 Lazy Import Pattern

```python
# ntrade/registry.py
def register_default_brokers():
    """Lazy registration — avoids importing Dhan-Tradehull at import time."""
    BrokerRegistry.register("paper", lambda: PaperBroker())
    
    @BrokerRegistry.lazy("dhan")
    def _create_dhan():
        from ntrade.brokers.dhan import DhanBroker
        return DhanBroker()
```

---

## 6. Dependency Flow — One-Way Outward

```
┌─────────────────────────────────────────────────────────────┐
│  USER CODE                                                   │
│  from ntrade import Market                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  ntrade.facade.Market                                        │
│    → BrokerRegistry.get("dhan")                              │
│    → InstrumentFactory.get("NIFTY", "NSE")                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  ntrade.registry.BrokerRegistry                              │
│    → lazy factory: DhanBroker()                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  ntrade.brokers.dhan.DhanBroker                              │
│    → DhanAuthProvider (auth lifecycle)                       │
│    → DhanTransport (API calls)                               │
│    → DhanMapper (normalization)                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Dhan-Tradehull (external SDK)                               │
│    → Tradehull instance                                      │
│    → REST API calls to Dhan                                  │
└─────────────────────────────────────────────────────────────┘

NO CIRCULAR DEPENDENCIES
Dependency direction: facade → registry → broker → SDK (one-way outward)
```

---

## 7. Key Design Patterns

### 7.1 Provider Pattern (DhanBroker Decomposition)

```python
class DhanBroker(BrokerAdapter):
    def __init__(self, ...):
        self._auth = DhanAuthProvider(...)      # Auth provider
        self._transport = DhanTransport(...)    # Transport provider
        self._mapper = DhanMapper()             # Mapper provider
```

**Benefit:** Each provider has a single responsibility and can be tested/replaced independently.

### 7.2 Capability Pattern (Dynamic Extensions)

```python
# Register once
@capability("depth20", brokers=("dhan",))
def _depth20(instrument, levels=20): ...

# Invoke anywhere
instrument.broker.depth20(levels=20)
```

**Benefit:** Broker-specific features don't pollute the base `BrokerAdapter` API. New capabilities can be added without modifying existing code (Open/Closed Principle).

### 7.3 Lazy Import Pattern

```python
# ntrade/registry.py
@BrokerRegistry.lazy("dhan")
def _create_dhan():
    from ntrade.brokers.dhan import DhanBroker  # imported only when needed
    return DhanBroker()
```

**Benefit:** Importing `ntrade` doesn't pull in `Dhan-Tradehull` or `pandas` until a Dhan broker is actually created. Fast imports, clean test isolation.

### 7.4 Auto-Refresh Token Pattern

```python
class DhanBroker:
    def _ensure_tsl(self):
        """Called before every critical operation."""
        new_tsl = self._auth.refresh_if_needed()
        if new_tsl is not self.tsl:
            self.tsl = new_tsl
            self._transport.tsl = new_tsl
```

**Benefit:** Token expiry is handled transparently. No API call ever hits an expired token. Background timer refreshes proactively 15 minutes before expiry.

---

## 8. Summary — What the Broker Module Exposes

| Layer | What It Exposes | How to Access |
|-------|----------------|---------------|
| **Abstract Interface** | `BrokerAdapter` (connect, quote, order, portfolio) | `from ntrade.brokers import BrokerAdapter` |
| **Concrete Brokers** | `DhanBroker`, `PaperBroker` | `Market(broker="dhan")` or `Market(broker="paper")` |
| **Capabilities** | 31 broker-specific extensions | `instrument.broker.<name>()` |
| **Registry** | Broker factory registry | `BrokerRegistry.register("upstox", factory)` |
| **Facade** | High-level `Market` API | `from ntrade import Market` |

**The broker module is a replaceable detail** (Uncle Bob). The domain layer never imports broker-specific code. All broker interaction goes through the `BrokerAdapter` interface or the capability facade.
