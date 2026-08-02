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


def test_context_from_env_forwards_gate_to_login_probe(monkeypatch):
    """T-035: the feed's login-time probe (get_tradehull -> _login_ok) must
    respect the session's BrokerRateGate when one is injected.

    ``_context_from_env`` does ``from ntrade.brokers.dhan_auth import
    get_tradehull`` INSIDE the method, so the patch must target
    ``ntrade.brokers.dhan_auth.get_tradehull`` (not ``dhan_feed.get_tradehull``).
    """
    import sys
    from types import SimpleNamespace

    from ntrade.execution.rate_limit import BrokerRateGate
    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    captured = {}

    def fake_get_tradehull(*, gate=None, **kwargs):
        captured["gate"] = gate
        tsl = SimpleNamespace(ClientCode="FAKE", token_id="tok")
        return tsl

    # Stub dhanhq so _context_from_env can build DhanContext offline.
    fake_dhanhq = SimpleNamespace(DhanContext=lambda cc, tok: SimpleNamespace(
        ClientCode=cc, token_id=tok))
    monkeypatch.setitem(sys.modules, "dhanhq", fake_dhanhq)
    monkeypatch.setattr("ntrade.brokers.dhan_auth.get_tradehull", fake_get_tradehull)

    gate = BrokerRateGate()
    feed = DhanMarketFeedSource(gate=gate)
    ctx = feed._context_from_env()
    assert captured["gate"] is gate
    assert ctx.ClientCode == "FAKE"


def test_context_from_env_gate_none_is_noop(monkeypatch):
    """T-035: without a gate the feed's probe path is unchanged (None passed)."""
    import sys
    from types import SimpleNamespace

    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    captured = {}

    def fake_get_tradehull(*, gate=None, **kwargs):
        captured["gate"] = gate
        return SimpleNamespace(ClientCode="FAKE", token_id="tok")

    fake_dhanhq = SimpleNamespace(DhanContext=lambda cc, tok: SimpleNamespace(
        ClientCode=cc, token_id=tok))
    monkeypatch.setitem(sys.modules, "dhanhq", fake_dhanhq)
    monkeypatch.setattr("ntrade.brokers.dhan_auth.get_tradehull", fake_get_tradehull)

    feed = DhanMarketFeedSource()  # no gate
    feed._context_from_env()
    assert captured["gate"] is None
