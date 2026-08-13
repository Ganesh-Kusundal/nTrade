# PE Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 3 scariest bugs from the PE review (silent engine failure, idempotency crash gap, kill switch un-trip) plus curate the public API and add a storage protocol.

**Architecture:** Minimal surgical changes to existing classes. No new abstractions, no new files unless unavoidable. Each task is independently testable.

**Tech Stack:** Python 3.12+, pytest, dataclasses, threading

## Global Constraints

- No new dependencies
- No new files unless the change cannot live in an existing file
- Every task ends with `pytest` green
- Ponytail: simplest change that fixes the root cause

## Skipped (YAGNI)

- **SymbolMaster scoping** — research showed it's already instance-level, not a global singleton. PE review overstated this.
- **Event queue / async bus** — too invasive, no concrete need yet. Revisit when a strategy actually blocks the pipeline.
- **Structured JSON logging** — nice-to-have, not blocking production.
- **Metrics export (Prometheus/StatsD)** — needs infra decisions, skip until deployed.
- **Property-based replay tests** — good but not blocking.

---

### Task 1: Handler exception rate counter in EventBus

**Files:**
- Modify: `ntrade/kernel/event_bus.py`
- Test: `tests/test_event_bus_handler_rate.py`

**Interfaces:**
- Consumes: existing `EventBus.publish()` exception handler at lines 71-77
- Produces: `EventBus.handler_error_count: int` property, `EventBus(max_handler_errors: int)` constructor param

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_bus_handler_rate.py
import pytest
from ntrade.kernel.event_bus import EventBus
from ntrade.events.base import Event
from ntrade.events.market import TickEvent
from datetime import datetime


def test_handler_error_count_starts_at_zero():
    bus = EventBus()
    assert bus.handler_error_count == 0


def test_handler_error_count_increments_on_exception():
    bus = EventBus()
    bus.subscribe(TickEvent, lambda e: 1 / 0)
    bus.publish(TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", ltp=1.0))
    assert bus.handler_error_count == 1


def test_max_handler_errors_publishes_risk_halt():
    from ntrade.events.risk import RiskHaltedEvent
    bus = EventBus(max_handler_errors=2)
    halted = []
    bus.subscribe(RiskHaltedEvent, lambda e: halted.append(e))
    bus.subscribe(TickEvent, lambda e: 1 / 0)
    tick = TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", ltp=1.0)
    bus.publish(tick)
    bus.publish(tick)
    assert bus.handler_error_count == 2
    assert len(halted) == 1
    assert "handler" in halted[0].reason.lower()


def test_handler_error_count_does_not_halt_by_default():
    bus = EventBus()  # no max_handler_errors
    bus.subscribe(TickEvent, lambda e: 1 / 0)
    tick = TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", ltp=1.0)
    for _ in range(100):
        bus.publish(tick)
    assert bus.handler_error_count == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_event_bus_handler_rate.py -v`
Expected: FAIL — `EventBus` has no `handler_error_count` or `max_handler_errors`

- [ ] **Step 3: Write minimal implementation**

In `ntrade/kernel/event_bus.py`, add to `EventBus.__init__`:

```python
def __init__(self, *, max_history: int = 10_000, max_handler_errors: int | None = None):
    # ... existing code ...
    self._handler_errors = 0
    self._max_handler_errors = max_handler_errors
```

Add property:

```python
@property
def handler_error_count(self) -> int:
    return self._handler_errors
```

Modify the exception handler in `publish()` (lines 71-77):

```python
        except Exception:
            self._handler_errors += 1
            _logger.error(
                "handler %s raised on %s (error #%d)",
                handler, type(event).__name__, self._handler_errors,
                exc_info=True,
            )
            if (
                self._max_handler_errors is not None
                and self._handler_errors >= self._max_handler_errors
            ):
                from ntrade.events.risk import RiskHaltedEvent
                self.publish(RiskHaltedEvent(
                    ts=event.ts,
                    reason=f"handler error limit reached ({self._handler_errors} errors)",
                ))
                self._max_handler_errors = None  # ponytail: halt once, don't re-halt
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_event_bus_handler_rate.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add ntrade/kernel/event_bus.py tests/test_event_bus_handler_rate.py
git commit -m "feat: add handler error rate counter to EventBus with optional halt threshold"
```

---

### Task 2: Kill switch latch in CircuitBreaker

**Files:**
- Modify: `ntrade/execution/_guard.py`
- Test: `tests/test_circuit_breaker_kill_latch.py`

**Interfaces:**
- Consumes: existing `CircuitBreaker` class
- Produces: `CircuitBreaker.trip_kill()` method, `CircuitBreaker._killed: bool` flag that blocks `_on_success()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_circuit_breaker_kill_latch.py
from ntrade.execution._guard import CircuitBreaker, CircuitState, CircuitBreakerConfig


def test_trip_kill_forces_open():
    cb = CircuitBreaker()
    cb.trip_kill()
    assert cb._state == CircuitState.OPEN


def test_on_success_does_not_untrip_after_kill():
    cb = CircuitBreaker()
    cb.trip_kill()
    cb._on_success()  # should NOT reset to CLOSED
    assert cb._state == CircuitState.OPEN


def test_on_success_works_normally_without_kill():
    cb = CircuitBreaker()
    cb._on_failure()
    cb._on_success()
    assert cb._state == CircuitState.CLOSED


def test_release_kill_restores_normal_behavior():
    cb = CircuitBreaker()
    cb.trip_kill()
    cb.release_kill()
    cb._on_success()
    assert cb._state == CircuitState.CLOSED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_circuit_breaker_kill_latch.py -v`
Expected: FAIL — `CircuitBreaker` has no `trip_kill` or `release_kill`

- [ ] **Step 3: Write minimal implementation**

In `ntrade/execution/_guard.py`, add to `CircuitBreaker.__init__`:

```python
    def __init__(self, config: CircuitBreakerConfig | None = None):
        # ... existing code ...
        self._killed = False
```

Add methods:

```python
    def trip_kill(self) -> None:
        """Force OPEN and latch — _on_success will NOT un-trip until release_kill()."""
        import time as _time
        with self._lock:
            self._killed = True
            self._state = CircuitState.OPEN
            self._opened_at = _time.monotonic()
            self._failures = self._config.failure_threshold

    def release_kill(self) -> None:
        """Remove the kill latch, allowing normal breaker recovery."""
        with self._lock:
            self._killed = False
```

Modify `_on_success()`:

```python
    def _on_success(self) -> None:
        with self._lock:
            if self._killed:
                return  # ponytail: kill latch blocks reset
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._half_open_probes = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_circuit_breaker_kill_latch.py -v`
Expected: PASS

- [ ] **Step 5: Update trip_kill_switch to use trip_kill()**

In `ntrade/execution/broker_executor.py`, replace the private state manipulation in `trip_kill_switch()` (lines 318-322):

```python
    # OLD (replace these 5 lines):
    # with self._breaker._lock:
    #     self._breaker._state = CircuitState.OPEN
    #     self._breaker._opened_at = _time.monotonic()
    #     self._breaker._failures = self._breaker._config.failure_threshold

    # NEW:
    self._breaker.trip_kill()
```

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add ntrade/execution/_guard.py ntrade/execution/broker_executor.py tests/test_circuit_breaker_kill_latch.py
git commit -m "fix: add kill latch to CircuitBreaker so _on_success cannot un-trip kill switch"
```

---

### Task 3: Connect LiveRunner to BrokerExecution.trip_kill_switch

**Files:**
- Modify: `ntrade/runner/live_runner.py`
- Test: `tests/test_live_runner_kill_connects_executor.py`

**Interfaces:**
- Consumes: `LiveRunner._on_risk_halted()`, `BrokerExecution.trip_kill_switch()`
- Produces: `_on_risk_halted` also calls `trip_kill_switch()` on any `BrokerExecution` targets

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_runner_kill_connects_executor.py
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime

from ntrade.runner.live_runner import LiveRunner
from ntrade.events.risk import RiskHaltedEvent
from ntrade.kernel.trading_session import TradingSession


def test_on_risk_halted_trips_broker_execution():
    session = TradingSession.paper()
    runner = LiveRunner(session.kernel)
    runner._broker_executor = MagicMock()

    event = RiskHaltedEvent(ts=datetime.now(), reason="test")
    runner._on_risk_halted(event)

    runner._broker_executor.trip_kill_switch.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_live_runner_kill_connects_executor.py -v`
Expected: FAIL — `_broker_executor` not tripped

- [ ] **Step 3: Write minimal implementation**

In `ntrade/runner/live_runner.py`, add to `__init__` (after kernel is stored):

```python
        self._broker_executor = None
```

In `_on_risk_halted()`, add after the existing kill switch loop (after line 312):

```python
        if self._broker_executor is not None:
            self._broker_executor.trip_kill_switch(reason=event.reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_live_runner_kill_connects_executor.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add ntrade/runner/live_runner.py tests/test_live_runner_kill_connects_executor.py
git commit -m "fix: connect LiveRunner kill switch to BrokerExecution.trip_kill_switch"
```

---

### Task 4: Persist idempotency guard via EventStore replay

**Files:**
- Modify: `ntrade/kernel/resilient.py`
- Test: `tests/test_idempotency_recovery.py`

**Interfaces:**
- Consumes: `ResilientKernel.recover()`, `EventStore.recovery_events()`, `BrokerExecution._idem`
- Produces: `_reseed_idempotency()` method that replays correlation_ids from OrderAcceptedEvent

- [ ] **Step 1: Write the failing test**

```python
# tests/test_idempotency_recovery.py
from datetime import datetime
from ntrade.storage.event_store import EventStore
from ntrade.events.order import OrderAcceptedEvent, OrderFilledEvent
from ntrade.execution._guard import MemoryIdempotencyGuard


def test_recovered_fills_seed_idempotency_guard():
    store = EventStore()
    store.append(OrderAcceptedEvent(
        ts=datetime.now(), order_id="BRK-001", symbol="REL",
        exchange="NSE", side="BUY", quantity=10, strategy="s1",
        correlation_id="corr-abc",
    ))
    store.append(OrderFilledEvent(
        ts=datetime.now(), order_id="BRK-001", symbol="REL",
        exchange="NSE", side="BUY", quantity=10, fill_price=100.0,
        strategy="s1", correlation_id="corr-abc",
    ))

    guard = MemoryIdempotencyGuard()
    # Replay correlation IDs from accepted orders
    from ntrade.events.order import OrderAcceptedEvent as OAE
    for event in store.recovery_events():
        if isinstance(event, OAE) and event.correlation_id:
            from ntrade.execution._guard import CorrelationId
            cid = CorrelationId(value=event.correlation_id)
            guard.record_result(cid, event.order_id)

    # Now a retry with the same correlation_id should be detected as duplicate
    from ntrade.execution._guard import CorrelationId
    result = guard.check_and_reserve(CorrelationId(value="corr-abc"))
    assert result is not None
    assert result.result == "BRK-001"
```

- [ ] **Step 2: Run test to verify it passes (it should — this tests the guard API)**

Run: `pytest tests/test_idempotency_recovery.py -v`
Expected: PASS (this validates the approach works with existing APIs)

- [ ] **Step 3: Wire into ResilientKernel._reseed_execution**

In `ntrade/kernel/resilient.py`, add to the end of `_reseed_execution()`:

```python
        # Rebuild idempotency guard from recovered OrderAcceptedEvents
        from ntrade.events.order import OrderAcceptedEvent
        for target in self.router._targets.values():
            if hasattr(target, '_idem') and self.recovery_store:
                for event in self.recovery_store.recovery_events():
                    if isinstance(event, OrderAcceptedEvent) and event.correlation_id:
                        from ntrade.execution._guard import CorrelationId
                        cid = CorrelationId(value=event.correlation_id)
                        target._idem.record_result(cid, event.order_id)
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add ntrade/kernel/resilient.py tests/test_idempotency_recovery.py
git commit -m "fix: rebuild idempotency guard from EventStore on crash recovery"
```

---

### Task 5: Curate public API

**Files:**
- Modify: `ntrade/__init__.py`

**Interfaces:**
- Consumes: existing `__all__` list
- Produces: trimmed `__all__` (~40 names instead of 78)

- [ ] **Step 1: Read current __all__ and identify removals**

Remove from `__all__`:
- Clock types: `TradingClock`, `LiveClock`, `ReplayClock`, `SimulationClock` (users never construct clocks directly)
- Infrastructure: `EventBus`, `RetryPolicy` (kernel internals)
- Broker-specific: `DhanMarketFeedSource`, `dhan_payload_to_events` (belongs in `ntrade.brokers.dhan`)
- Simulation internals: `SimTick`, `synthesize_1m_ticks` (belongs in `ntrade.backtest`)
- Cost constants: `STATUTORY_DEFAULT` (internal default)
- Execution internals: `ExecutionRouter` (users interact through session)

- [ ] **Step 2: Trim __all__**

In `ntrade/__init__.py`, replace `__all__`:

```python
__all__ = [
    # Session
    "TradingSession",
    # Instruments
    "Instrument",
    "Equity", "Index", "ETF", "Currency", "Commodity", "Bond", "Crypto", "Spot",
    "Future", "Option", "OptionChain", "SyntheticInstrument",
    # Market data
    "Quote", "Tick", "CandleSeries", "DepthLevel", "MarketDepth", "Greeks",
    # Orders
    "Order", "OrderSide", "OrderType", "OrderStatus", "TradeType",
    # State
    "MarketState", "SessionState",
    # Factory
    "Market", "InstrumentFactory", "BrokerRegistry", "SymbolMaster",
    # Events
    "Event",
    "TickEvent", "QuoteEvent", "DepthEvent", "CandleClosedEvent",
    "QuoteUpdatedEvent", "IndicatorUpdatedEvent",
    "WatchlistReady",
    "OrderIntentEvent", "OrderAcceptedEvent", "OrderRejectedEvent", "OrderFilledEvent",
    "OrderUpdatedEvent", "OrderTimeoutEvent",
    "PositionUpdatedEvent", "BalanceChangedEvent",
    "SignalGeneratedEvent", "SignalApprovedEvent", "SignalRejectedEvent",
    "RiskHaltedEvent", "RiskResumedEvent",
    "KernelStartedEvent", "SessionStartedEvent", "SessionStoppedEvent",
    "RunnerStartedEvent", "RunnerStoppedEvent",
    "HeartbeatEvent", "FeedDisconnectedEvent",
    # Strategy
    "Strategy",
    # Kernel
    "TradingKernel", "ResilientKernel", "StrategyRunner",
    # Execution
    "SimulatedExecution", "BrokerExecution",
    # Costs
    "FixedSlippage", "PercentageSlippage", "FlatCommission", "PercentageCommission",
    "IndianStatutoryCosts",
    # Storage / Backtest
    "EventStore", "ReplayEngine", "BacktestSimulator", "BacktestResult", "FillPolicy",
    "BarAwareExecution",
    # Feed sources
    "MarketFeedSource", "SimulatedFeedSource", "SyntheticMarketFeedSource",
    "LiveRunner",
    # Scanning
    "Scanner", "ScannerFacade", "ScannerResult", "ScreenerFacade",
    # Constants
    "Exchange", "Timeframe", "DEFAULT_TIMEFRAME",
    # Version
    "__version__",
]
```

- [ ] **Step 3: Verify removed names still importable from submodules**

Run: `python -c "from ntrade.brokers.dhan import DhanMarketFeedSource; from ntrade.kernel.event_bus import EventBus; print('OK')"`
Expected: OK (names still exist, just not in top-level `__all__`)

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass (no test should import removed names from top-level `ntrade`)

- [ ] **Step 5: Commit**

```bash
git add ntrade/__init__.py
git commit -m "refactor: curate public API — remove broker-specific, clock, and internal exports"
```

---

### Task 6: HistoryStorage Protocol

**Files:**
- Create: `ntrade/data/protocols.py`
- Modify: `ntrade/data/parquet_store.py`
- Test: `tests/test_storage_protocol.py`

**Interfaces:**
- Consumes: `ParquetStorage` method signatures
- Produces: `HistoryStorage` Protocol with 5 methods

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_protocol.py
from ntrade.data.protocols import HistoryStorage
from ntrade.data.parquet_store import ParquetStorage
import tempfile
import pandas as pd
from datetime import datetime


def test_parquet_storage_satisfies_protocol():
    with tempfile.TemporaryDirectory() as tmp:
        store = ParquetStorage(tmp)
        assert isinstance(store, HistoryStorage)


def test_protocol_has_required_methods():
    assert hasattr(HistoryStorage, 'upsert')
    assert hasattr(HistoryStorage, 'read')
    assert hasattr(HistoryStorage, 'symbols')
    assert hasattr(HistoryStorage, 'date_range')
    assert hasattr(HistoryStorage, 'clear')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage_protocol.py -v`
Expected: FAIL — `ntrade.data.protocols` does not exist

- [ ] **Step 3: Write minimal implementation**

```python
# ntrade/data/protocols.py
from typing import Protocol, runtime_checkable
from datetime import datetime
import pandas as pd


@runtime_checkable
class HistoryStorage(Protocol):
    """Protocol for OHLCV storage backends.

    ponytail: ParquetStorage is the only implementation today.
    Add TimescaleDB/InfluxDB by implementing these 5 methods.
    """

    def upsert(self, df: pd.DataFrame) -> int: ...
    def read(
        self,
        symbols: list[str] | None = None,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        timeframe: str | None = None,
    ) -> pd.DataFrame: ...
    def symbols(self) -> list[str]: ...
    def date_range(
        self, symbol: str, timeframe: str | None = None
    ) -> tuple[datetime, datetime] | None: ...
    def clear(self) -> None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage_protocol.py -v`
Expected: PASS (`@runtime_checkable` + structural subtyping = ParquetStorage satisfies it automatically)

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add ntrade/data/protocols.py tests/test_storage_protocol.py
git commit -m "feat: add HistoryStorage Protocol for storage backend abstraction"
```

---

### Task 7: Add correlation_id to key log outputs

**Files:**
- Modify: `ntrade/kernel/event_bus.py`
- Modify: `ntrade/execution/broker_executor.py`
- Test: `tests/test_correlation_id_logging.py`

**Interfaces:**
- Consumes: existing log calls in EventBus and BrokerExecution
- Produces: log lines that include `correlation_id` when available

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correlation_id_logging.py
import logging
from datetime import datetime
from ntrade.kernel.event_bus import EventBus
from ntrade.events.market import TickEvent


def test_bus_exception_log_includes_correlation_id(caplog):
    bus = EventBus()
    bus.subscribe(TickEvent, lambda e: 1 / 0)
    tick = TickEvent(
        ts=datetime.now(), symbol="X", exchange="NSE", ltp=1.0,
        correlation_id="abc-123",
    )
    with caplog.at_level(logging.ERROR, logger="ntrade.bus"):
        bus.publish(tick)
    assert "abc-123" in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_correlation_id_logging.py -v`
Expected: FAIL — correlation_id not in log output

- [ ] **Step 3: Write minimal implementation**

In `ntrade/kernel/event_bus.py`, modify the exception handler:

```python
        except Exception:
            self._handler_errors += 1
            _logger.error(
                "handler %s raised on %s correlation_id=%s (error #%d)",
                handler, type(event).__name__,
                getattr(event, 'correlation_id', None),
                self._handler_errors,
                exc_info=True,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_correlation_id_logging.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add ntrade/kernel/event_bus.py tests/test_correlation_id_logging.py
git commit -m "feat: include correlation_id in EventBus handler exception logs"
```

---

## Execution Order

Tasks 1-2 are independent — can run in parallel.
Task 3 depends on Task 2 (uses `trip_kill()`).
Task 4 is independent.
Tasks 5-7 are independent.

Recommended parallel groups:
- **Group A:** Tasks 1, 2, 4, 5, 6, 7 (all independent)
- **Group B:** Task 3 (after Task 2 completes)
