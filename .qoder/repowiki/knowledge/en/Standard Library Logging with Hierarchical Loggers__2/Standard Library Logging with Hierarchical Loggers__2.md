---
kind: logging_system
name: Standard Library Logging with Hierarchical Loggers
category: logging_system
scope:
    - '**'
source_files:
    - ntrade/kernel/event_bus.py
    - ntrade/execution/broker_executor.py
    - ntrade/runner/live_runner.py
    - ntrade/engines/strategy_engine.py
    - ntrade/domain/analytics/indicators.py
    - ntrade/brokers/dhan_auth_provider.py
    - ntrade/sources/dhan_feed.py
    - ntrade/storage/event_store.py
---

The nTrade framework uses Python's built-in `logging` module exclusively — no third-party logging libraries (structlog, loguru, etc.) are present. Each subsystem defines its own logger via `logging.getLogger("ntrade.<subsystem>")`, producing a hierarchical namespace under the `ntrade.*` root.

**Framework and initialization**
- Every module that logs creates a module-level `logger = logging.getLogger(__name__)` or a fixed name like `"ntrade.execution"`, `"ntrade.strategy"`, `"ntrade.bus"`, `"ntrade.runner"`, `"ntrade.indicators"`, `"ntrade.storage"`, `"ntrade.feed.dhan"`.
- There is no centralized `logging.basicConfig()` or `dictConfig` call in the codebase; handlers and formatters are not configured by the framework itself. Output configuration is left to the application entry point or test harnesses, which use `caplog.at_level(...)` to capture logs during tests.

**Log levels and usage patterns**
- `debug`: internal state transitions (e.g., proactive token refresh scheduling).
- `info`: operational milestones (order fills, runner heartbeats, successful token refresh).
- `warning`: recoverable anomalies (rate-limit backoff, stale orders evicted, order timeouts, feed warmup failures).
- `error`: handler exceptions on the event bus, feed watchdog halts.
- `critical`: unrecoverable conditions requiring manual intervention (orphan broker orders with unmappable side/status, kill-switch activation/deactivation failures).
- `exception`: used when re-raising caught exceptions (strategy hook failures) so tracebacks are included.

**Structured fields and message format**
- All messages use positional `%`-style formatting with named semantic keys embedded in the message text (e.g., `"proactive_refresh_scheduled: delay=%.0fs expiry=%.0fs"`, `"filled %s %s x%d @ %.2f (order %s)"`). No JSON or structured dict payloads are emitted; correlation_id/causation_id are attached to events themselves rather than to log records.
- The event bus enriches each event with `correlation_id` and `causation_id` at publish time, but these fields are not propagated into log records.

**Sinks and routing**
- No file handlers, rotating loggers, or external sinks are wired in the framework. Logs flow through the default stderr handler unless overridden externally.
- Tests assert log output by capturing per-logger level via `pytest.caplog.at_level(logging.ERROR, logger="ntrade.feed.dhan")`, confirming that each subsystem's logger name is the routing key.

**Conventions observed**
- One logger per module/subsystem, all namespaced under `ntrade.*`.
- Exception paths always include `exc_info=True` or use `logger.exception(...)` so stack traces are captured.
- Critical operational safety events (orphan adoption, kill-switch failures) are elevated to `critical` to force operator attention.