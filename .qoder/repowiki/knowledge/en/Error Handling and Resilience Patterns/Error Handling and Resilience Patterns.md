---
kind: error_handling
name: Error Handling and Resilience Patterns
category: error_handling
scope:
    - '**'
source_files:
    - ntrade/kernel/resilient.py
    - ntrade/execution/retry.py
    - ntrade/brokers/base.py
    - ntrade/brokers/dhan.py
    - ntrade/kernel/event_bus.py
    - ntrade/brokers/capabilities.py
    - ntrade/brokers/dhan_auth.py
---

The nTrade framework employs a layered error-handling strategy that combines Python's built-in exception hierarchy with domain-specific resilience infrastructure. There is no centralized custom exception type hierarchy; instead, the codebase uses standard exceptions (`ValueError`, `RuntimeError`, `AttributeError`, `ConnectionError`, `ImportError`) raised at appropriate boundaries, combined with retry policies and event-bus fault isolation for operational resilience.

**Exception usage by layer:**
- **Broker adapters (base.py)**: Unimplemented optional capabilities raise `NotImplementedError` with descriptive messages indicating which broker feature is unsupported. This enables capability detection via duck typing rather than explicit checks.
- **Dhan broker (dhan.py)**: Network/API failures are wrapped in `RuntimeError` with context about the failing operation (e.g., "Dhan order rejected", "Dhan cancel failed"), preserving the original exception via `from exc`. Validation errors use `ValueError` (e.g., invalid timeframes). Missing configuration raises `ValueError`; authentication failures raise `ConnectionError`.
- **Kernel/resilient.py**: State-machine violations raise `RuntimeError` (e.g., calling `recover()` twice, missing recovery store, registering strategies before recovery).
- **Event bus (event_bus.py)**: Handler exceptions are caught and logged — individual handler failures never propagate to the kernel or other subscribers.

**Resilience infrastructure:**
- `execution/retry.py` provides `RetryPolicy` (exponential backoff with jitter) and `RateLimiter` (token bucket) as stdlib-only, frozen dataclass utilities designed for hot-path broker API calls. The `execute` method retries on any `Exception` and re-raises the last exception after all attempts.
- `kernel/resilient.py` implements crash recovery via `ResilientKernel`, which replays recorded events from an `EventStore` to deterministically rebuild state without re-trading.

**Event bus fault isolation:**
The `EventBus.publish` method wraps each handler invocation in try/except, logging exceptions with `exc_info=True` and continuing dispatch to other handlers. This ensures one faulty subscriber cannot crash the entire event pipeline.

**Logging convention:**
Each module defines a module-level logger via `logging.getLogger(__name__)` (e.g., `ntrade.bus`, `ntrade.execution`, `ntrade.runner`). Errors are logged at appropriate levels (`error` for handler failures, `warning` for transient issues like indicator computation failures, `info` for operational milestones).

**No global error middleware:**
There is no centralized error middleware or exception translation layer. Error handling is localized to the boundary where it occurs — brokers wrap external failures, the event bus isolates handler faults, and callers handle specific exception types.