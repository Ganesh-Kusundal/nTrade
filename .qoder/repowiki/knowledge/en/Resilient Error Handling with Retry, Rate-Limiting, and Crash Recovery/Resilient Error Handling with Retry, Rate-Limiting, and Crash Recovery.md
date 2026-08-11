---
kind: error_handling
name: Resilient Error Handling with Retry, Rate-Limiting, and Crash Recovery
category: error_handling
scope:
    - '**'
source_files:
    - ntrade/kernel/resilient.py
    - ntrade/execution/retry.py
    - ntrade/execution/rate_limit.py
    - ntrade/execution/broker_executor.py
    - ntrade/brokers/base.py
    - ntrade/brokers/dhan.py
    - ntrade/sources/dhan_feed.py
    - ntrade/kernel/event_bus.py
---

The ntrade framework implements a layered error-handling strategy centered on resilience, structured exceptions, and deterministic recovery rather than ad-hoc try/except blocks. Errors are categorized by severity and domain, propagated as Python exceptions, and handled at architectural boundaries (event bus, broker transport, execution layer) to keep the core kernel stable.

**Exception taxonomy and propagation**
- Domain and validation errors use standard Python exceptions (`ValueError`, `AttributeError`) for parameter validation in backtest/simulator code.
- Broker capability gaps signal missing features via `NotImplementedError` raised from `BrokerAdapter` base-class stubs (option chains, order cancellation/modification, books, balance, positions, holdings).
- Runtime failures in broker adapters raise `RuntimeError` with contextual messages (e.g., "DhanBroker is not connected", "get_quote failed… LTP is 0", bracket-order rejections, cancel/modify failures), chained via `from exc` to preserve tracebacks.
- A dedicated `RateLimited(RuntimeError)` exception marks DH-904 quota exhaustion; `is_rate_limited(exc)` detects it by type or text markers (`dh-904`, `rate_limit`, `429`, `too many requests`).
- The event bus swallows handler exceptions — one bad subscriber never crashes the kernel — and logs them with full traceback.

**Retry and backoff infrastructure**
- `RetryPolicy` (frozen dataclass) wraps flaky calls with exponential backoff + jitter, configured via `max_retries`, `base_delay`, `max_delay`, `multiplier`, `jitter`, and an optional `no_retry_on` predicate.
- Rate-limit exceptions are never retried by default: `_is_no_retry` returns `True` for anything `is_rate_limited` matches, preventing quota amplification (B-011).
- `RateLimiter` provides a thread-safe token-bucket limiter for simple throttling (e.g., feed reconnect pacing).

**Rate limiting as a first-class concern**
- `BrokerRateGate` enforces multi-window sliding windows per `Quota` class (`QUOTE`, `DATA`, `ORDER`, `NON_TRADING`) matching Dhan's documented limits.
- `acquire()` blocks until all windows have capacity; `penalize()` applies DH-904 cooldowns so rejected calls do not immediately re-fire.
- `status()` exposes per-quota telemetry (`windows`, `cooldown_remaining`, `blocked`) for pre-deploy checks and dashboards.
- `BLOCKED_MIN_WINDOW_SPAN_S = 60.0` ensures short burst windows don't falsely flag the system as blocked.

**Crash recovery and state reconciliation**
- `ResilientKernel` extends `TradingKernel` to rebuild state from a recorded `EventStore` causal stream (market events + fills) after a crash, without re-trading.
- Recovery is one-shot, must run before strategies are registered, and re-seeds execution counters and open-order trackers to avoid collisions.
- `BrokerExecution.reconcile_open()` adopts orphaned broker-side orders that were accepted by the exchange but whose placement response was lost, emitting fills through normal channels.
- `BrokerExecution.restore_open()` rehydrates the in-memory `_open` map from stored deltas so partial fills resume correctly post-recovery.

**Event-driven error signaling**
- Feed disconnections publish `FeedDisconnectedEvent` with a reason string; the dhan feed source catches websocket errors, logs them, publishes the event, and triggers a rate-limited reconnect loop protected by an `RLock` to prevent concurrent teardown/rebuild races.
- Order lifecycle errors surface as typed events: `OrderRejectedEvent`, `OrderTimeoutEvent`, `OrderUpdatedEvent`, `OrderFilledEvent` — never as raw exceptions to the strategy layer.
- The kernel records every published event to an `EventStore` when provided, enabling audit trails and deterministic replay.

**Conventions observed**
- Broker adapter methods raise `NotImplementedError` for unsupported capabilities rather than returning sentinel values.
- Network/broker failures propagate as exceptions up to the execution layer, which converts them into domain events or logs warnings and continues.
- Rate-limit exceptions are detected uniformly via `is_rate_limited()` and never retried unless explicitly overridden.
- Handler exceptions in the event bus are caught and logged; they do not propagate to the publisher.
- Reconnect attempts are serialized and rate-limited to avoid thundering-herd scenarios.