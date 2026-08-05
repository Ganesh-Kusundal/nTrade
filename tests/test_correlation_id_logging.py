import logging
from datetime import datetime

from ntrade.kernel.event_bus import EventBus
from ntrade.events.market import TickEvent


def test_bus_exception_log_includes_correlation_id(caplog):
    bus = EventBus()
    bus.subscribe(TickEvent, lambda e: 1 / 0)
    tick = TickEvent(
        ts=datetime.now(),
        symbol="X",
        exchange="NSE",
        price=1.0,
        correlation_id="abc-123",
    )
    with caplog.at_level(logging.ERROR, logger="ntrade.bus"):
        bus.publish(tick)
    assert "abc-123" in caplog.text
