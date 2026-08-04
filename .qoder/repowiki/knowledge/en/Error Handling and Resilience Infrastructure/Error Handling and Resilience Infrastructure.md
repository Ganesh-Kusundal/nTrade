---
kind: error_handling
name: Error Handling and Resilience Infrastructure
category: error_handling
scope:
    - '**'
source_files:
    - ntrade/execution/retry.py
    - ntrade/kernel/event_bus.py
    - ntrade/kernel/resilient.py
    - ntrade/brokers/base.py
    - ntrade/brokers/dhan.py
    - ntrade/execution/broker_executor.py
---

The nTrade framework implements error handling through a layered approach combining Python's built-in exceptions, structured logging, retry/backoff policies, and crash-recovery mechanisms. There is no centralized custom exception hierarchy; instead, the codebase uses standard Python exceptions with consistent patterns across modules.

**Exception Types and Propagation:**
The broker layer (particularly `ntrade/brokers/dhan.py`) raises `RuntimeError` for operational failures like connection issues, rejected orders, and API errors, while using `ValueError` for parameter validation and `NotImplementedError` for unsupported broker capabilities in the base adapter. Rate-limiting errors are handled specially through the `RateLimited` exception from the rate limiting infrastructure, which is never retried to avoid amplifying quota exhaustion.

**Retry and Backoff Strategy:**
The `ntrade/execution/retry.py` module provides a `RetryPolicy` dataclass implementing exponential backoff with jitter. It automatically excludes rate-limit failures (`RateLimited` or anything matching `is_rate_limited()`) from retries, as documented in the module docstring: "retrying a DH-904 only burns more quota." The policy supports configurable parameters including `max_retries`, `base_delay`, `max_delay`, `multiplier`, and `jitter`, plus a `no_retry_on` callback for custom exclusion logic.

**Event Bus Error Isolation:**
The `ntrade/kernel/event_bus.py` implements fault isolation by catching and logging handler exceptions without propagating them, ensuring one bad subscriber cannot crash the entire kernel. Exceptions are logged at ERROR level with full traceback information, and the event dispatch continues to other handlers.

**Crash Recovery and Resilience:**
The `ntrade/kernel/resilient.py` module provides `ResilientKernel`, which can rebuild trading state from a recorded EventStore after crashes. It replays causal events (market data + fills) deterministically before resuming live trading, maintaining the zero-parity invariant across live, replay, and backtest modes. Recovery includes rebuilding open order trackers and resequencing order IDs to prevent collisions.

**Structured Logging:**
The codebase uses Python's standard `logging` module with module-specific loggers (e.g., `ntrade.bus`, `ntrade.execution`, `ntrade.runner`). Errors are logged at appropriate levels (ERROR for critical failures, WARNING for recoverable issues like stale orders, INFO for normal operations). The event bus logs handler exceptions with full context including handler identity and event type.

**Broker-Specific Error Patterns:**
Broker implementations follow consistent patterns: they raise `RuntimeError` for transport failures, set order status to `REJECTED` before raising exceptions for failed operations, and propagate `RateLimited` exceptions unchanged. The Dhan broker specifically handles edge cases like MARKET order conversion for F&O instruments and bracket order routing.

**Stale Order Detection:**
The `ntrade/execution/broker_executor.py` implements timeout detection for pending orders, emitting `OrderTimeoutEvent` when orders remain PENDING beyond a threshold (5 minutes), and evicting stale orders after repeated polling failures.