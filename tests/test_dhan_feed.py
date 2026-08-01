"""DhanMarketFeedSource tests."""


def test_on_error_is_logged(caplog):
    import logging
    from ntrade.sources.dhan_feed import DhanMarketFeedSource
    feed = DhanMarketFeedSource()
    with caplog.at_level(logging.ERROR, logger="ntrade.feed.dhan"):
        feed._on_error(None, RuntimeError("connection lost"))
    assert any("connection lost" in r.message for r in caplog.records)

def test_on_close_publishes_disconnect_event():
    from ntrade.sources.dhan_feed import DhanMarketFeedSource
    from ntrade.events.lifecycle import FeedDisconnectedEvent
    from ntrade.kernel.event_bus import EventBus
    from ntrade.kernel.clock import LiveClock
    from unittest.mock import MagicMock

    bus = EventBus()
    received = []
    bus.subscribe(FeedDisconnectedEvent, lambda e: received.append(e))

    kernel = MagicMock()
    kernel.clock = LiveClock()
    kernel.bus = bus

    feed = DhanMarketFeedSource(kernel=kernel)
    feed._on_close(None)
    assert len(received) == 1
    assert received[0].reason == "websocket closed"
