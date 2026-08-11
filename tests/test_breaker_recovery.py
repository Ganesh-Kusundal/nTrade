"""BrokerExecution circuit breaker must recover after cooldown (not park
order tracking forever)."""

from datetime import datetime
from unittest.mock import MagicMock

from ntrade.events.order import OrderIntentEvent
from ntrade.execution._guard import CircuitBreaker, CircuitBreakerConfig, CircuitState
from ntrade.execution.broker_executor import BrokerExecution
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus


def _live_ctx(instrument):
    bus = EventBus()
    clock = LiveClock()
    return TradingContext(bus, clock, mode="live",
                          instruments={instrument.symbol: instrument}, session_id="")


def _inject_open(exe, order_id="TEST-001", symbol="X"):
    intent = OrderIntentEvent(
        symbol=symbol, exchange="NSE", side="BUY", quantity=10,
        order_type="LIMIT", price=100.0, ts=datetime.now(),
    )
    order = MagicMock()
    order.order_id = order_id
    order.status = MagicMock()
    order.status.value = "PENDING"
    exe._open[order_id] = {
        "intent": intent, "order": order, "filled": 0,
        "status": order.status, "placed_at": datetime.now(),
    }
    return order


def test_poll_half_opens_and_recovers_after_cooldown():
    """An OPEN circuit (from accumulated transport failures) must allow a
    probe after cooldown: poll() then re-checks the order and closes the
    circuit on success, instead of parking order tracking forever."""
    from ntrade.execution.rate_limit import RateLimited, Quota

    bus = EventBus()
    clock = LiveClock()
    ctx = TradingContext(bus, clock, mode="live", instruments={}, session_id="")

    broker = MagicMock()
    broker.get_order_status.side_effect = RuntimeError("down")

    breaker = CircuitBreaker(CircuitBreakerConfig(
        failure_threshold=2, cooldown_seconds=0.0))  # cooldown already elapsed
    exe = BrokerExecution(ctx, broker, circuit_breaker=breaker)

    order = _inject_open(exe)

    # Trip the breaker open via poll failures (2 failures >= threshold).
    exe.poll()
    assert breaker.state == CircuitState.CLOSED
    exe.poll()
    assert breaker.state == CircuitState.OPEN
    # The order is still tracked (below the stale eviction limit).
    assert "TEST-001" in exe._open

    # Broker comes back: next poll must half-open and recover the order.
    broker.get_order_status.side_effect = None
    exe.poll()
    assert breaker.state == CircuitState.CLOSED
    assert "TEST-001" in exe._open  # recovered, not evicted

    # Success should have reset the stale counter.
    record = exe._open["TEST-001"]
    assert record["stale"] == 0


def test_poll_open_circuit_skips_broker_during_cooldown():
    """While the cooldown has NOT elapsed, poll must not call the broker."""
    bus = EventBus()
    clock = LiveClock()
    ctx = TradingContext(bus, clock, mode="live", instruments={}, session_id="")

    broker = MagicMock()
    broker.get_order_status.side_effect = RuntimeError("down")
    breaker = CircuitBreaker(CircuitBreakerConfig(
        failure_threshold=1, cooldown_seconds=60.0))
    exe = BrokerExecution(ctx, broker, circuit_breaker=breaker)

    _inject_open(exe)
    exe.poll()  # 1 failure >= threshold=1 → OPEN
    assert breaker.state == CircuitState.OPEN

    calls_before = broker.get_order_status.call_count
    exe.poll()  # cooldown not elapsed → skip broker
    assert broker.get_order_status.call_count == calls_before
    assert breaker.state == CircuitState.OPEN
