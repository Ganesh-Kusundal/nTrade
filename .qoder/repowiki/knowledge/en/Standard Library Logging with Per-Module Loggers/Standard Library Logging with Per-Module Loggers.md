---
kind: logging_system
name: Standard Library Logging with Per-Module Loggers
category: logging_system
scope:
    - '**'
source_files:
    - ntrade/kernel/event_bus.py
    - ntrade/execution/broker_executor.py
    - ntrade/runner/live_runner.py
    - ntrade/sources/dhan_feed.py
    - ntrade/storage/event_store.py
    - ntrade/brokers/dhan_auth_provider.py
---

The ntrade framework uses Python's built-in `logging` module exclusively — no third-party logging libraries (loguru, structlog, etc.) are imported. Each module creates its own logger via `logging.getLogger(__name__)` or a domain-prefixed name such as `ntrade.execution`, `ntrade.bus`, `ntrade.runner`, `ntrade.feed.dhan`, and `ntrade.storage`. There is no centralized logging configuration file; handlers, formatters, and log levels are not configured at import time in any of the observed modules, meaning output defaults to stderr with the standard `WARNING` level unless callers configure it externally.

Log messages follow a consistent pattern across subsystems:
- Human-readable, positional-format strings (e.g. `logger.info("filled %s %s x%d @ %.2f (order %s)", ...)`).
- Structured context fields are passed as additional arguments rather than encoded into the message string (e.g. `order_id`, `symbol`, `side`, `quantity`, `strategy`, `ts`).
- Error paths use `_logger.error(..., exc_info=True)` for stack traces (EventBus handler exceptions), while warnings cover recoverable conditions like stale orders, feed disconnects, and failed reconnects.
- Critical failures (kill-switch activation/deactivation) use `logger.critical` to ensure visibility.

No global `basicConfig` call was found in the codebase; tests configure capture via `caplog.at_level()` against specific logger names, indicating that runtime configuration is expected to be provided by the application entry point or test harness. The EventStore persists events to JSONL for replay/audit but does not write logs itself — it only warns about torn lines during load.