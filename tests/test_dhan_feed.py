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


def test_context_from_env_reuses_broker_tsl(monkeypatch):
    """D-018: the feed reuses an already-authenticated broker tsl from kernel
    context instead of running a second get_tradehull probe chain."""
    import sys
    from types import SimpleNamespace

    from ntrade.kernel.context import TradingContext
    from ntrade.kernel.event_bus import EventBus
    from ntrade.kernel.clock import LiveClock
    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    probe_calls = []

    def fake_get_tradehull(*, gate=None, **kwargs):
        probe_calls.append(gate)
        raise AssertionError("D-018: probe chain must be skipped when broker tsl exists")

    fake_dhanhq = SimpleNamespace(DhanContext=lambda cc, tok: SimpleNamespace(
        ClientCode=cc, token_id=tok))
    monkeypatch.setitem(sys.modules, "dhanhq", fake_dhanhq)
    monkeypatch.setattr("ntrade.brokers.dhan_auth.get_tradehull", fake_get_tradehull)

    broker = SimpleNamespace(tsl=SimpleNamespace(ClientCode="REAL", token_id="realtok"))
    inst = SimpleNamespace(broker_adapter=broker, symbol="NIFTY")
    ctx = TradingContext(EventBus(), LiveClock())
    ctx.register(inst)
    kernel = SimpleNamespace(ctx=ctx)

    feed = DhanMarketFeedSource(kernel=kernel)
    context = feed._context_from_env()
    assert context.ClientCode == "REAL"
    assert context.token_id == "realtok"
    assert probe_calls == []  # get_tradehull never invoked


def test_context_from_env_no_broker_tsl_falls_back_to_probe(monkeypatch):
    """D-018: when no instrument exposes a usable tsl, the feed still falls
    back to the get_tradehull probe chain."""
    import sys
    from types import SimpleNamespace

    from ntrade.kernel.context import TradingContext
    from ntrade.kernel.event_bus import EventBus
    from ntrade.kernel.clock import LiveClock
    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    probe_calls = []

    def fake_get_tradehull(*, gate=None, **kwargs):
        probe_calls.append(gate)
        return SimpleNamespace(ClientCode="FAKE", token_id="tok")

    fake_dhanhq = SimpleNamespace(DhanContext=lambda cc, tok: SimpleNamespace(
        ClientCode=cc, token_id=tok))
    monkeypatch.setitem(sys.modules, "dhanhq", fake_dhanhq)
    monkeypatch.setattr("ntrade.brokers.dhan_auth.get_tradehull", fake_get_tradehull)

    # Instrument exists but its broker exposes no usable tsl (e.g. paper).
    inst = SimpleNamespace(broker_adapter=SimpleNamespace(tsl=None), symbol="NIFTY")
    ctx = TradingContext(EventBus(), LiveClock())
    ctx.register(inst)
    kernel = SimpleNamespace(ctx=ctx)

    feed = DhanMarketFeedSource(kernel=kernel)
    context = feed._context_from_env()
    assert context.ClientCode == "FAKE"
    assert len(probe_calls) == 1


def test_start_stop_serialized_by_lifecycle_lock():
    """D-020: concurrent stop()/start() callers cannot double-close or
    rebuild the single-use websocket concurrently."""
    import threading
    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    class FakeFeed:
        def __init__(self):
            self.close_count = 0
            self.start_count = 0

        def start(self):
            self.start_count += 1
            return object()  # thread-like

        def close_connection(self):
            self.close_count += 1

    feed = DhanMarketFeedSource(feed_factory=lambda subs: FakeFeed())
    feed._feed = FakeFeed()  # simulate an already-built feed
    feed._last_close_count = 0

    # Track close calls across the shared fake feed.
    real_close = feed._feed.close_connection
    feed._feed.close_connection = lambda: (real_close(), setattr(feed, "_last_close_count", feed._last_close_count + 1))
    feed._reconnect_limiter = type("NoWait", (), {"wait": lambda self: None})()

    errors = []

    def stopper():
        try:
            for _ in range(5):
                feed.stop()
        except Exception as exc:  # pragma: no cover - unexpected
            errors.append(exc)

    threads = [threading.Thread(target=stopper) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert feed._feed is None
    assert not feed.running
    # The feed was closed exactly once overall (idempotent stop): each
    # thread raced but the RLock serialized close + cache-drop.
    assert feed._last_close_count == 1


def test_stop_is_idempotent_and_running_flag_clears():
    """D-020: stop() without a feed is a no-op; running flag is cleared."""
    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    feed = DhanMarketFeedSource()
    feed.running = True
    feed.stop()
    assert not feed.running
    assert feed._feed is None
    feed.stop()  # second stop must not raise


def _no_wait(feed):
    feed._reconnect_limiter = type("NoWait", (), {"wait": lambda self: None})()
    return feed


def test_on_close_unexpected_reconnects():
    """M-2: a broker-side close (no stop() call) must rebuild the socket."""
    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    class FakeFeed:
        def start(self):
            return object()

        def close_connection(self):
            pass

    builds = []

    def factory(subs):
        builds.append(FakeFeed())
        return builds[-1]

    feed = _no_wait(DhanMarketFeedSource(feed_factory=factory))
    feed.start()
    assert len(builds) == 1
    feed._on_close(None)  # unexpected close — no stop() armed the flag
    assert len(builds) == 2  # rebuilt a fresh single-use socket
    assert feed.running
    assert feed._reconnect_called


def test_on_close_after_stop_does_not_reconnect():
    """M-2: a deliberate stop() leaves the feed down — the close callback
    fired by the teardown must not rebuild the websocket."""
    from ntrade.sources.dhan_feed import DhanMarketFeedSource

    class FakeFeed:
        def start(self):
            return object()

        def close_connection(self):
            pass

    builds = []

    def factory(subs):
        builds.append(FakeFeed())
        return builds[-1]

    feed = _no_wait(DhanMarketFeedSource(feed_factory=factory))
    feed.start()
    feed.stop()
    feed._on_close(None)  # callback racing the teardown
    assert len(builds) == 1  # never rebuilt
    assert not feed.running
