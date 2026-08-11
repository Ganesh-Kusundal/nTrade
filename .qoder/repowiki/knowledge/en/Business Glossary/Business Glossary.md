---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### zero-parity
- Definition：Core invariant that the same event stream through TradingKernel(mode=...) produces identical fills across live, replay, and backtest modes. The engine stack is mode-independent; only the event source, execution target, and clock differ. This is the single most important correctness guarantee for a trading system.
- Aliases：zero parity、parity

### capability
- Definition：Broker-specific feature registered via @capability decorator without modifying base classes. Enables open/closed principle — new brokers add capabilities without domain code changes. Examples include depth20, kill_switch, margin_calculator. The BrokerExtensionFacade dynamically dispatches capability names to registered functions.
- Aliases：@capability、capability pattern

### kill switch
- Definition：Risk circuit breaker mechanism that halts all trading activity when predefined thresholds are breached (daily loss, drawdown, price deviation). Wired through LiveRunner to call instrument.broker.kill_switch(action="ACTIVATE") on every instrument. Failure to activate must be logged and escalated — silent failure leaves the broker accepting orders with no risk oversight.
- Aliases：risk halt、circuit breaker

### EventStore
- Definition：Persistent append-only log of canonical events used for crash recovery and replay. Stores JSON-encoded events per file. Currently opens/closes file handle per event (I/O bottleneck), has no file locking or fsync, and crashes on unknown event types during decode. Critical for ResilientKernel's one-shot recovery without re-running strategies.
- Aliases：event store、event log

### ReplayClock
- Definition：Deterministic time source injected into the kernel for replay/backtest modes. Replaces datetime.now() to ensure simulation time drives all timestamp-dependent logic. The kernel's own docstring states engines and strategies never call datetime.now() directly — they ask the trading clock instead. However, 20+ call sites in domain/broker layers still violate this invariant.
- Aliases：trading clock、simulation clock

### ResilientKernel
- Definition：Kernel wrapper that provides crash recovery by replaying the causal event stream (market data + fills) without re-running strategies. Recording is paused during recovery, execution sequence is reseeded, and recovery is one-shot. Requires EventStore to function.
- Aliases：crash recovery kernel

### PositionSyncEngine
- Definition：Background engine that synchronizes local position state with broker reality. On failure returns None (not empty list) so the kernel keeps previous state rather than silently wiping positions on network blips. Runs independently of strategy execution.
- Aliases：position sync

### LiveStream
- Definition：Per-instrument user-facing callback lifecycle managing subscription state, tick caching, and on_tick/on_quote decorator API. Distinct from EventBus: EventBus handles kernel-level inter-engine communication, while LiveStream manages user-level subscriptions and callbacks. Serves different purpose than EventBus and should not be removed.
- Aliases：tick stream、subscription manager

### OptionChain navigation
- Definition：Structured way to navigate options contracts through Expiry and OptionPair objects. Methods include expiries(), atm(), calls(), puts(), otm(), itm(), greeks(), iv(), oi(). Returns Instruments (not symbols) so scanner output flows directly into trading without conversion.
- Aliases：option chain、expiry navigation

### Scanner subsystem
- Definition：Pluggable market scanning framework returning Instrument lists (not symbols). Built-in scanners include GapScanner, VolumeSpikeScanner, MomentumScanner, BreakoutScanner, ImbalanceScanner. ScannerResult is a frozen dataclass with ranking support via top(). Integrated through TradingSession.scanner() facade.
- Aliases：scanner、market scanner

### Provider decomposition
- Definition：Architecture pattern splitting broker implementation into focused services: Authentication, Symbol Mapping, Transport (REST/WebSocket), and Capabilities. DhanBroker (1095 lines) currently mixes all concerns — the goal is to extract into DhanAuth, DhanMapper, DhanTransport, and capability modules so adding Upstox becomes a plugin exercise.
- Aliases：provider pattern、broker decomposition

### TradingSession
- Definition：Unified entry point replacing dual entry points (Market + TradingKernel). Single interface for connect("dhan"), paper(), replay(events) with identical APIs regardless of provider. Composes InstrumentFactory, Portfolio, Account, event routing, and extension discovery. Must resolve naming conflict with existing domain/session.py TradingSession dataclass.
- Aliases：session、trading session
