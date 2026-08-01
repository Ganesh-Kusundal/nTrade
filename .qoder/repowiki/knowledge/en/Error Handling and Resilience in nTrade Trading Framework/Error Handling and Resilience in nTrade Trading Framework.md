---
kind: error_handling
name: Error Handling and Resilience in nTrade Trading Framework
category: error_handling
scope:
    - '**'
source_files:
    - ntrade/brokers/dhan_transport.py
    - ntrade/execution/retry.py
    - ntrade/kernel/event_bus.py
    - ntrade/kernel/resilient.py
    - ntrade/sources/dhan_feed.py
    - ntrade/execution/broker_executor.py
    - ntrade/brokers/base.py
---

The nTrade framework implements a layered error handling strategy that prioritizes resilience, observability, and crash recovery across its event-driven trading kernel. The approach combines structured exceptions, retry policies, graceful degradation, and deterministic recovery mechanisms.

**Core Error Types and Propagation**

The framework defines specific exception types for different failure modes. `BrokerDataError` (in `ntrade/brokers/dhan_transport.py`) is raised when market data operations fail after retries or return degenerate values like zero LTP prices, preventing silent corruption of PnL calculations. This follows the principle that critical failures must propagate upward rather than being silently collapsed to safe defaults.

Standard Python exceptions are used throughout: `ValueError` for invalid parameters (e.g., backtest fill orders), `NotImplementedError` for unsupported broker capabilities, and generic `Exception` catches for transient network failures. The transport layer wraps lower-level exceptions with contextual messages while preserving the original cause via `from exc` chaining.

**Retry and Resilience Infrastructure**

The `RetryPolicy` class in `ntrade/execution/retry.py` provides exponential backoff with jitter for flaky operations like LTP fetching. It's configured with `max_retries=3`, `base_delay=0.2s`, and `max_delay=5.0s` by default. The policy is applied selectively — critical operations like order placement don't use automatic retries, while market data endpoints do.

A thread-safe `RateLimiter` uses token bucket algorithm to prevent API rate limiting violations, particularly important for high-frequency market data operations.

**Event Bus Error Isolation**

The `EventBus` in `ntrade/kernel/event_bus.py` implements fault isolation through exception swallowing. Handler exceptions are caught and logged but never propagate to other subscribers or the core loop. This ensures one buggy subscriber cannot crash the entire trading system. Each handler execution is wrapped in try-catch blocks with detailed logging including stack traces.

**Graceful Degradation Patterns**

Market data operations follow a consistent pattern: attempt the operation, catch exceptions, and return safe defaults (empty DataFrames, None values, or zero quantities). For example, `get_depth()` returns `None` on timeout, `get_historical()` returns empty `CandleSeries`, and various metadata methods return empty dicts or lists.

The Dhan feed source (`ntrade/sources/dhan_feed.py`) demonstrates robust payload parsing where malformed data is skipped without breaking the kernel. Unknown security IDs, non-dict payloads, and malformed fields are handled gracefully.

**Crash Recovery and State Rebuild**

The `ResilientKernel` in `ntrade/kernel/resilient.py` provides deterministic state recovery from an `EventStore`. After a crash, it replays the causal stream (market events + fills) to rebuild instrument, candle, indicator, and portfolio state before resuming live trading. This maintains the "zero-parity" invariant between backtest and live execution.

Recovery includes rebuilding open-order trackers, reseeding execution sequences to avoid ID collisions, and ensuring strategies aren't re-registered during replay to prevent double-trading.

**Logging and Observability**

Structured logging is used throughout with module-specific loggers (e.g., `ntrade.execution`, `ntrade.feed.dhan`). Critical failures use `logger.critical()`, operational issues use `logger.error()`, and warnings use `logger.warning()`. All exceptions include `exc_info=True` for full stack traces.

**Timeout and Staleness Detection**

Order lifecycle management includes staleness detection — if broker status polling fails repeatedly (default 10 times), orders are evicted from tracking to prevent memory leaks. Timeout detection flags PENDING orders older than 5 minutes as potentially stuck.

**Constraints and Conventions**

- Market data failures should raise `BrokerDataError` rather than returning zeros to prevent silent corruption
- Event handlers must not raise exceptions that could crash the bus
- Transport methods should wrap external calls with appropriate error handling
- Recovery operations must be idempotent and one-shot
- Logging should distinguish between recoverable and fatal errors
- Graceful degradation is preferred over hard failures for non-critical operations