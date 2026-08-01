---
kind: logging_system
name: Standard Library Logging with Per-Module Loggers
category: logging_system
scope:
    - '**'
source_files:
    - ntrade/domain/analytics/indicators.py
    - ntrade/execution/broker_executor.py
    - ntrade/kernel/event_bus.py
    - ntrade/runner/live_runner.py
    - ntrade/sources/dhan_feed.py
    - tests/test_observability.py
---

The nTrade framework uses Python's built-in `logging` module exclusively — no third-party logging libraries (loguru, structlog, etc.) are used. Each module that needs logging creates a module-level logger via `logging.getLogger("ntrade.<component>")`, following a consistent naming convention that mirrors the package hierarchy.

**Logger naming convention:**
- `ntrade.indicators` — domain analytics indicators
- `ntrade.execution` — broker execution and order lifecycle
- `ntrade.bus` — event bus dispatch
- `ntrade.runner` — live runner orchestration
- `ntrade.feed.dhan` — Dhan market feed source

**Log levels used:**
- `info`: routine operational messages (order fills, poll results, fill details)
- `warning`: recoverable issues (stale orders, timeout detection, indicator failures)
- `error`: non-fatal errors (feed errors, handler exceptions with full traceback)
- `critical`: severe failures (kill-switch activation failures)

**Structured fields approach:**
The codebase does not use structured JSON logging. Instead, it uses positional string formatting in log messages, embedding key context directly into the message text (e.g., `"filled %s %s x%d @ %.2f (order %s)"`). There is no centralized formatter or log rotation configured at the application level.

**No global configuration:**
There is no `logging.basicConfig()` call anywhere in the codebase. Log handlers and output destinations are not configured within the application code itself — this is left to the entry point or test harness. The test suite demonstrates this by using `caplog.at_level(logging.INFO, logger="ntrade.execution")` to capture logs during tests.

**Error handling patterns:**
- Exception handling includes `exc_info=True` for error-level logs to capture tracebacks
- Failures are logged rather than silently swallowed (e.g., indicator computation failures log warnings)
- Critical operations like kill-switch activation failures are logged at critical level

**Test coverage:**
The `tests/test_observability.py` file specifically validates that broker fill events produce log records at INFO level under the `ntrade.execution` logger.