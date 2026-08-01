"""Event model, EventBus and TradingClock tests (kernel Slice A)."""

from datetime import datetime

import pytest

from ntrade.events.base import Event
from ntrade.events.market import TickEvent
from ntrade.kernel.clock import LiveClock, ReplayClock, SimulationClock, TradingClock
from ntrade.kernel.event_bus import EventBus


def test_event_is_frozen_and_hashable():
    e = TickEvent(ts=datetime(2026, 1, 1), symbol="NIFTY", exchange="NSE", price=24500.5)
    assert e.price == 24500.5
    assert e.event_id and isinstance(e.event_id, str)
    with pytest.raises(Exception):
        e.price = 1.0  # frozen dataclass
    assert hash(e) is not None


def test_event_ids_are_unique():
    ts = datetime(2026, 1, 1)
    a = TickEvent(ts=ts, symbol="NIFTY", exchange="NSE", price=1.0)
    b = TickEvent(ts=ts, symbol="NIFTY", exchange="NSE", price=1.0)
    assert a.event_id != b.event_id


def test_bus_dispatches_exact_and_base_subscribers():
    bus = EventBus()
    seen = []
    bus.subscribe(TickEvent, lambda e: seen.append(("tick", e.symbol)))
    bus.subscribe(Event, lambda e: seen.append(("any", e.symbol)))  # base type
    bus.publish(TickEvent(ts=datetime.now(), symbol="NIFTY", exchange="NSE", price=100.0))
    assert ("tick", "NIFTY") in seen
    assert ("any", "NIFTY") in seen


def test_bus_unsubscribe():
    bus = EventBus()
    calls = []

    def handler(e):
        calls.append(e)

    bus.subscribe(TickEvent, handler)
    bus.unsubscribe(TickEvent, handler)
    bus.publish(TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=1.0))
    assert calls == []


def test_bus_isolates_handler_errors():
    bus = EventBus()
    calls = []

    def boom(e):
        raise RuntimeError("handler failed")

    def ok(e):
        calls.append(e)

    bus.subscribe(TickEvent, boom)
    bus.subscribe(TickEvent, ok)
    bus.publish(TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=1.0))
    assert len(calls) == 1


def test_bus_history_and_clear():
    bus = EventBus()
    bus.publish(TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=1.0))
    assert len(bus.history) == 1
    bus.clear()
    assert len(bus) == 0 and bus.history == []


def test_live_clock():
    assert isinstance(LiveClock().now(), datetime)


def test_replay_clock_set_and_advance():
    clock = ReplayClock(start=datetime(2026, 1, 1, 9, 15))
    assert clock.now() == datetime(2026, 1, 1, 9, 15)
    clock.set(datetime(2026, 1, 1, 15, 30))
    assert clock.now() == datetime(2026, 1, 1, 15, 30)
    clock.advance(minutes=5)
    assert clock.now() == datetime(2026, 1, 1, 15, 35)


def test_simulation_clock_speed():
    clock = SimulationClock(speed=100.0)
    assert clock.speed == 100.0
    assert isinstance(clock.now(), datetime)


def test_base_clock_raises():
    with pytest.raises(NotImplementedError):
        TradingClock().now()


def test_bus_history_bounded_by_max_history():
    bus = EventBus(max_history=5)
    for i in range(10):
        bus.publish(TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=float(i)))
    assert len(bus.history) == 5
    assert bus.history[0].price == 5.0
    assert bus.history[-1].price == 9.0


def test_bus_logs_handler_exceptions(caplog):
    import logging
    bus = EventBus()
    def bad_handler(event):
        raise ValueError("boom")
    bus.subscribe(TickEvent, bad_handler)
    with caplog.at_level(logging.ERROR, logger="ntrade.bus"):
        bus.publish(TickEvent(ts=datetime.now(), symbol="X", exchange="NSE", price=1.0))
    assert any("raised on" in r.message for r in caplog.records)

def test_heartbeat_event_publishable():
    from ntrade.events.lifecycle import HeartbeatEvent
    bus = EventBus()
    received = []
    bus.subscribe(HeartbeatEvent, lambda e: received.append(e))
    bus.publish(HeartbeatEvent(tick_count=42, open_orders=3, ts=datetime.now()))
    assert len(received) == 1
    assert received[0].tick_count == 42
    assert received[0].open_orders == 3
