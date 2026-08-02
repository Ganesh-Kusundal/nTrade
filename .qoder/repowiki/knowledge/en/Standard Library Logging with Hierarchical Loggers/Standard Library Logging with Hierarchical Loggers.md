---
kind: logging_system
name: Standard Library Logging with Hierarchical Loggers
category: logging_system
scope:
    - '**'
source_files:
    - ntrade/brokers/dhan_auth_provider.py
    - ntrade/domain/analytics/indicators.py
    - ntrade/execution/broker_executor.py
    - ntrade/kernel/event_bus.py
    - ntrade/runner/live_runner.py
    - ntrade/sources/dhan_feed.py
---

The nTrade framework uses Python's built-in `logging` module exclusively — no third-party logging libraries (loguru, structlog, etc.) are imported in the core codebase. Each module creates its own logger via `logging.getLogger(__name__)` or a dotted name under the `ntrade.*` namespace, producing a hierarchical logger tree that mirrors the package structure.

**Logger naming convention**: Modules use either `logging.getLogger(__name__)` (e.g. `dhan_auth_provider.py`, `sources/dhan_feed.py`) or explicit dotted names like `ntrade.indicators`, `ntrade.execution`, `ntrade.bus`, and `ntrade.runner`. This yields loggers named `ntrade.brokers.dhan_auth_provider`, `ntrade.sources.dhan_feed`, `ntrade.domain.analytics.indicators`, `ntrade.execution.broker_executor`, `ntrade.kernel.event_bus`, and `ntrade.runner.live_runner`.

**Log levels used consistently**:
- `logger.debug()` for operational details (proactive refresh scheduling)
- `logger.info()` for significant state changes (token refresh success, order fills, poll counts)
- `logger.warning()` for recoverable problems (indicator computation failures, stale orders, proactive refresh failures, feed close)
- `logger.error()` for handler exceptions in the event bus, feed errors, and warmup failures
- `logger.critical()` only for kill-switch activation failures

**Structured field patterns**: Messages embed key context as positional arguments to the format string rather than using keyword fields or structured JSON. Examples include `"order %s stale after %d polls — evicting"`, `"filled %s %s x%d @ %.2f (order %s)"`, `"feed error: %s"`, and `"indicator %s failed on %d rows: %s"`. No JSON serialization or dedicated structured-log library is used.

**No centralized configuration**: There is no root logger configuration file, no `basicConfig()` call in the application entry points, and no shared logging setup module. Each module simply imports `logging` and calls `getLogger(...)`. Handlers, formatters, and log levels must be configured externally by the process that imports these modules. The event bus itself swallows handler exceptions and logs them at `error` level with `exc_info=True`, ensuring one bad subscriber cannot crash the kernel.

**Print statements**: A single `print()` call exists in `ntrade/domain/scanner.py` for signal output; all other runtime output goes through the logging system. Example scripts under `.agents/skills/` and `check_connection.py` use `print()` for user-facing output, but this is outside the core framework.