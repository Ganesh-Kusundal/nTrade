---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### Zero Parity
- Definition：Architectural invariant ensuring that the same event stream produces identical results across live trading, replay, and backtest environments. The core principle that strategies run identically regardless of whether events originate from a live broker, replay file, or historical simulator.
- Aliases：zero-parity、parity invariant

### Trading Kernel
- Definition：Central orchestrator component that wires together all trading engines, manages event flow, and coordinates the complete trading pipeline from market data ingestion through execution and portfolio updates.
- Aliases：kernel、trading_kernel

### Event Bus
- Definition：Synchronous publish-subscribe messaging system that decouples trading components. Uses MRO-based subscription where handlers inherit from base classes automatically receive relevant events.
- Aliases：bus、event_bus

### Broker Adapter
- Definition：Abstraction layer that normalizes broker-specific APIs into domain objects. Implements the BrokerAdapter ABC pattern allowing multiple brokers (PaperBroker, DhanBroker) to plug in without changing domain logic.
- Aliases：adapter、broker_adapter

### Capability Pattern
- Definition：Open/closed extension mechanism allowing broker-specific features to be registered dynamically. Enables adding new broker capabilities without modifying base instrument classes.
- Aliases：capability、capabilities

### ResilientKernel
- Definition：Crash recovery mechanism built on top of EventStore that replays causal market events to reconstruct trading state after failures. Ensures deterministic recovery without re-executing strategies.
- Aliases：resilient_kernel、recovery

### LiveStream
- Definition：Per-instrument observer target managing subscription lifecycle, tick caching, and callback registration for real-time market data. Distinct from EventBus which handles inter-engine communication.
- Aliases：stream、live_stream

### PositionSyncEngine
- Definition：Background reconciliation engine that synchronizes kernel's internal position state with broker-reported positions. Acts as source of truth for actual holdings while kernel maintains derived state for strategy decisions.
- Aliases：position_sync、sync_engine

### Risk Circuit Breaker
- Definition：Safety mechanism that halts trading when predefined risk thresholds are breached (daily loss, drawdown, price deviation). Automatically activates kill switch on brokers when triggered.
- Aliases：risk_breaker、circuit_breaker

### BacktestSimulator
- Definition：Deterministic simulation engine that replays historical OHLCV data through the same trading kernel, producing identical decisions as live trading for validation and testing.
- Aliases：simulator、backtest_simulator
