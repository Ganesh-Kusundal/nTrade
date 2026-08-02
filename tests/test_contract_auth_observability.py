"""Graph-aware contract (Item A): auth shutdown is independent of observability.

DhanBroker.stop() must cancel the auth provider's token-refresh timer. The auth
lifetime is deliberately separate from observability events (Heartbeat /
FeedDisconnected / OrderTimeout) — DhanBroker and DhanAuthProvider have NO event
bus and publish none at all, so the only observable side effect of stop() is the
timer cancellation below. That separation (not a fake bus) is what pins the
"auth ↔ observability are unrelated" contract.
"""
import threading

from ntrade.brokers.dhan import DhanBroker


def test_stop_prevents_auth_timer_and_emits_no_heartbeat():
    broker = DhanBroker(connect=False)

    # Schedule a genuinely pending refresh timer the same way
    # _schedule_proactive_refresh() does, so stop() has a real timer to cancel.
    broker._auth._refresh_timer = threading.Timer(3600.0, lambda: None)
    broker._auth._refresh_timer.daemon = True

    broker.stop()

    # (a) The auth provider's refresh timer is cancelled (set back to None).
    assert broker._auth._refresh_timer is None

    # (b) Contract guard: the broker layer has no bus/publish path, so its
    # shutdown cannot emit observability events. If it ever grows one, this
    # assertion fails loudly.
    assert not hasattr(broker, "bus")
    assert not hasattr(broker, "publish")