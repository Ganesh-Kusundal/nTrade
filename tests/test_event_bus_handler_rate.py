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
    bus.publish(TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=1.0))
    assert bus.handler_error_count == 1


def test_max_handler_errors_publishes_risk_halt():
    from ntrade.events.risk import RiskHaltedEvent
    bus = EventBus(max_handler_errors=2)
    halted = []
    bus.subscribe(RiskHaltedEvent, lambda e: halted.append(e))
    bus.subscribe(TickEvent, lambda e: 1 / 0)
    tick = TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=1.0)
    bus.publish(tick)
    bus.publish(tick)
    assert bus.handler_error_count == 2
    assert len(halted) == 1
    assert "handler" in halted[0].reason.lower()


def test_handler_error_count_does_not_halt_by_default():
    bus = EventBus()  # no max_handler_errors
    bus.subscribe(TickEvent, lambda e: 1 / 0)
    tick = TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=1.0)
    for _ in range(100):
        bus.publish(tick)
    assert bus.handler_error_count == 100
