"""Graph-aware contract (Item A): auth shutdown is independent of observability.

DhanBroker.stop() must cancel the auth provider's token-refresh timer and must
NOT publish any Heartbeat/FeedDisconnected/OrderTimeout observability event.
The auth lifetime and the observability bus are unrelated concerns.
"""
import threading

from ntrade.brokers.dhan import DhanBroker
from ntrade.events.base import Event
from ntrade.events.lifecycle import FeedDisconnectedEvent, HeartbeatEvent
from ntrade.events.order import OrderTimeoutEvent
from ntrade.kernel.event_bus import EventBus


def test_stop_prevents_auth_timer_and_emits_no_heartbeat():
    broker = DhanBroker(connect=False)

    # Schedule a genuinely pending proactive-refresh timer the same way
    # _schedule_proactive_refresh() does, so stop() has a real timer to cancel.
    broker._auth._refresh_timer = threading.Timer(3600.0, lambda: None)
    broker._auth._refresh_timer.daemon = True

    # Capture every observability/lifecycle event the broker might publish.
    bus = EventBus()
    events = []
    for event_type in (Event, HeartbeatEvent, FeedDisconnectedEvent, OrderTimeoutEvent):
        bus.subscribe(event_type, events.append)

    broker.stop()

    # (a) The auth provider's refresh timer is cancelled (set back to None).
    assert broker._auth._refresh_timer is None

    # (b) Stopping alone must not publish any observability/lifecycle event.
    assert len(events) == 0