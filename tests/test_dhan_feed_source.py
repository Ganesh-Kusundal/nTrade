"""DhanMarketFeedSource + pure payload mapper tests (Slice F3).

The mapper is fully offline — it takes parsed dhanhq payload dicts and yields
canonical events. The source wraps a dhanhq MarketFeed; injected via
``feed_factory`` so the wiring is testable without live credentials.
"""

from datetime import datetime

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import DepthEvent, QuoteEvent, TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.sources.dhan_feed import DhanMarketFeedSource, dhan_payload_to_events


def _ts():
    return datetime(2026, 1, 1, 9, 15, 30)


SYMBOL_MAP = {2885: ("RELIANCE", "NSE")}


TICKER_PAYLOAD = {
    "type": "Ticker Data", "exchange_segment": 1, "security_id": 2885,
    "LTP": "2450.00", "LTT": "09:15:30",
}

QUOTE_PAYLOAD = {
    "type": "Quote Data", "exchange_segment": 1, "security_id": 2885,
    "LTP": "2450.00", "LTQ": 10, "LTT": "09:15:30",
    "avg_price": "2445.50", "volume": 1234567,
    "total_sell_quantity": 450000, "total_buy_quantity": 500000,
    "open": "2430.00", "close": "2440.00", "high": "2465.00", "low": "2425.00",
}

FULL_PAYLOAD = {
    "type": "Full Data", "exchange_segment": 1, "security_id": 2885,
    "LTP": "368.15", "LTQ": 50, "LTT": "09:15:30",
    "avg_price": "365.00", "volume": 10000,
    "total_sell_quantity": 2500, "total_buy_quantity": 3000,
    "OI": 1250000, "oi_day_high": 1265000, "oi_day_low": 1210000,
    "open": "360.00", "close": "355.00", "high": "372.00", "low": "352.00",
    "depth": [
        {"bid_quantity": 100, "ask_quantity": 75, "bid_orders": 2, "ask_orders": 1,
         "bid_price": "368.10", "ask_price": "368.20"},
        {"bid_quantity": 90, "ask_quantity": 60, "bid_orders": 1, "ask_orders": 1,
         "bid_price": "368.00", "ask_price": "368.30"},
    ],
}


# ------------------------------------------------------------------ pure mapper
def test_mapper_ticker_produces_tick():
    events = dhan_payload_to_events(TICKER_PAYLOAD, SYMBOL_MAP, _ts())
    assert len(events) == 1
    tick = events[0]
    assert isinstance(tick, TickEvent)
    assert tick.symbol == "RELIANCE" and tick.exchange == "NSE"
    assert tick.price == 2450.0
    assert tick.ts == _ts()


def test_mapper_quote_produces_quote_and_tick():
    events = dhan_payload_to_events(QUOTE_PAYLOAD, SYMBOL_MAP, _ts())
    kinds = {type(e) for e in events}
    assert kinds == {TickEvent, QuoteEvent}
    quote = next(e for e in events if isinstance(e, QuoteEvent))
    assert quote.ltp == 2450.0
    assert quote.open == 2430.0 and quote.high == 2465.0
    assert quote.low == 2425.0 and quote.prev_close == 2440.0
    assert quote.volume == 1234567


def test_mapper_full_produces_quote_tick_and_depth():
    events = dhan_payload_to_events(FULL_PAYLOAD, SYMBOL_MAP, _ts())
    kinds = {type(e) for e in events}
    assert kinds == {TickEvent, QuoteEvent, DepthEvent}
    depth = next(e for e in events if isinstance(e, DepthEvent))
    assert depth.bids[0] == (368.10, 100, 2)
    assert depth.asks[1] == (368.30, 60, 1)
    quote = next(e for e in events if isinstance(e, QuoteEvent))
    assert quote.oi == 1250000


def test_mapper_unknown_security_skipped():
    events = dhan_payload_to_events({**TICKER_PAYLOAD, "security_id": 9999},
                                    SYMBOL_MAP, _ts())
    assert events == []


def test_mapper_malformed_payload_never_crashes():
    events = dhan_payload_to_events({**TICKER_PAYLOAD, "LTP": "oops"}, SYMBOL_MAP, _ts())
    assert events == []  # price unparseable → skip, no raise


def test_mapper_string_security_id_lookup():
    events = dhan_payload_to_events(TICKER_PAYLOAD, {"2885": ("R", "NSE")}, _ts())
    assert events and events[0].symbol == "R"


def test_mapper_empty_payload_returns_empty():
    assert dhan_payload_to_events({}, SYMBOL_MAP, _ts()) == []


def test_mapper_market_depth_produces_depth_and_tick():
    depth_payload = {**TICKER_PAYLOAD, "type": "Market Depth",
                     "depth": [{"bid_price": "50", "ask_price": "50.05",
                                "bid_quantity": 10, "ask_quantity": 20,
                                "bid_orders": 1, "ask_orders": 2}]}
    events = dhan_payload_to_events(depth_payload, SYMBOL_MAP, _ts())
    kinds = {type(e) for e in events}
    assert kinds == {TickEvent, DepthEvent}
    depth = next(e for e in events if isinstance(e, DepthEvent))
    assert depth.bids[0] == (50.0, 10, 1)
    assert depth.asks[0] == (50.05, 20, 2)


def test_mapper_non_dict_payload_skipped():
    # dhanhq passes status strings ("Markets Open") and None (disconnect)
    assert dhan_payload_to_events("Markets Open", SYMBOL_MAP, _ts()) == []
    assert dhan_payload_to_events(None, SYMBOL_MAP, _ts()) == []
    assert dhan_payload_to_events(5, SYMBOL_MAP, _ts()) == []


def test_mapper_malformed_int_fields_never_crash():
    payload = {**QUOTE_PAYLOAD, "LTQ": "oops", "volume": "x", "OI": None}
    events = dhan_payload_to_events(payload, SYMBOL_MAP, _ts())
    assert events  # still yields tick + quote, with safe zeroed ints
    tick = next(e for e in events if isinstance(e, TickEvent))
    assert tick.quantity == 0


def test_mapper_depth_malformed_level_skipped():
    payload = {**TICKER_PAYLOAD, "type": "Full Data",
               "depth": [None, {"bid_price": "oops", "ask_price": "1.0"},
                         {"bid_price": "9.0", "ask_price": "9.05",
                          "bid_quantity": "z", "ask_quantity": 2,
                          "bid_orders": 1, "ask_orders": 1}]}
    events = dhan_payload_to_events(payload, SYMBOL_MAP, _ts())
    depth = next((e for e in events if isinstance(e, DepthEvent)), None)
    assert depth is not None
    assert depth.bids == ((9.0, 0, 1),)


# ------------------------------------------------------------------ source wiring
class FakeFeed:
    """A stand-in for dhanhq.MarketFeed that records the callback wiring."""

    def __init__(self, subscriptions):
        self.subscriptions = subscriptions
        self.started = False
        self.closed = False
        self.handler = None
        self.thread = None

    def start(self):
        self.started = True
        self.thread = object()  # like dhanhq returns a Thread
        return self.thread

    def close_connection(self):
        self.closed = True


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock())
    k.register(Equity("RELIANCE"))
    return k


def test_source_injects_feed_factory():
    k = _kernel()
    captured = {}

    def factory(subscriptions):
        captured["subs"] = subscriptions
        return FakeFeed(subscriptions)

    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=factory)
    src.start()
    assert captured["subs"] == [(1, "2885", 21)]  # Full = 21; SecurityId must be str
    assert isinstance(src._feed, FakeFeed)
    assert src._feed.started is True


def test_feed_always_uses_full_mode_code():
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)],
                               feed_factory=lambda subs: FakeFeed(subs))
    assert src._subscriptions() == [(1, "2885", 21)]
    assert not hasattr(src, "version")
    assert not hasattr(src, "mode")


def test_index_subscription_uses_quote_mode_not_full():
    """Dhan's IDX segment silently drops Full(21) subscriptions — indices
    must subscribe in Quote(17) mode or they never deliver ticks (verified
    live 2026-08-06)."""
    src = DhanMarketFeedSource(symbols=[(0, 13), (0, 25)],
                               feed_factory=lambda subs: FakeFeed(subs))
    assert src._subscriptions() == [(0, "13", 17), (0, "25", 17)]


def test_mixed_segments_pick_mode_per_symbol():
    """Equities/F&O keep Full(21); indices in the same batch use Quote(17)."""
    src = DhanMarketFeedSource(symbols=[(0, 13), (1, 2885), (2, 49081)],
                               feed_factory=lambda subs: FakeFeed(subs))
    assert src._subscriptions() == [(0, "13", 17), (1, "2885", 21), (2, "49081", 21)]


def test_subscriptions_coerce_int_security_ids_to_str():
    """Int security IDs silently yield a connected-but-empty dhanhq feed."""
    src = DhanMarketFeedSource(symbols=[(1, 2885), (2, 58072)],
                               feed_factory=lambda subs: FakeFeed(subs))
    assert src._subscriptions() == [(1, "2885", 21), (2, "58072", 21)]
    # Already-string IDs stay strings (no double-wrap / drift).
    src2 = DhanMarketFeedSource(symbols=[(1, "2885")],
                                feed_factory=lambda subs: FakeFeed(subs))
    assert src2._subscriptions() == [(1, "2885", 21)]


def test_source_on_message_publishes_to_kernel():
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: FakeFeed(subs))
    # wire the callback exactly like dhanhq does: on_message(feed, payload)
    feed = src._build_feed()
    feed.handler = src._on_message
    feed.handler(feed, TICKER_PAYLOAD)
    feed.handler(feed, FULL_PAYLOAD)
    assert src.payloads_ingested == 2
    inst = k.ctx.instrument("RELIANCE")
    assert inst.market.ltp() == 368.15  # last tick
    assert len([e for e in k.bus.history if isinstance(e, TickEvent)]) == 2
    assert len([e for e in k.bus.history if isinstance(e, DepthEvent)]) == 1


def test_source_on_message_ignores_unknown_symbol():
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 9999)],
                               symbol_map={9999: ("UNKNOWN", "NSE")},
                               feed_factory=lambda subs: FakeFeed(subs))
    src._on_message(src._feed or FakeFeed([]), {**TICKER_PAYLOAD, "security_id": 9999})
    assert src.payloads_ingested == 1
    assert len(k.bus.history) == 1  # tick published, engine no-op for unknown inst


def test_source_on_error_and_close_are_safe():
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: FakeFeed(subs))
    # M-2: error/close now trigger a reconnect (never raise) — the feed is
    # rebuilt and running again; only a deliberate stop() leaves it down.
    src._reconnect_limiter = type("NoWait", (), {"wait": lambda self: None})()
    src._on_error(None, RuntimeError("boom"))  # must not raise
    src._on_close(None)
    assert src.running is True
    src.stop()
    assert src.running is False


def test_source_start_then_stop():
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: FakeFeed(subs))
    src.start()
    assert src.running is True
    feed = src._feed
    src.stop()
    assert feed.closed is True
    assert src.running is False


def test_source_requires_dhanhq_when_no_factory(monkeypatch):
    """Without a feed factory, building the feed needs dhanhq installed."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "dhanhq":
            raise ImportError("no module named 'dhanhq'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    src = DhanMarketFeedSource(symbols=[(1, 2885)], symbol_map=SYMBOL_MAP)
    with pytest.raises(ImportError, match="dhanhq"):
        src._build_feed()


def test_source_restart_after_stop_rebuilds_feed():
    k = _kernel()
    builds = []
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: builds.append(subs) or FakeFeed(subs))
    src.start()
    first = src._feed
    src.stop()
    assert src._feed is None  # cache dropped so a fresh feed is built
    src.start()
    assert src._feed is not None and src._feed is not first
    assert len(builds) == 2  # factory invoked again
    assert src.running is True


def test_source_non_dict_payload_does_not_crash_kernel():
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: FakeFeed(subs))
    src._on_message(src._feed or FakeFeed([]), "Markets Open")  # status packet
    src._on_message(src._feed or FakeFeed([]), None)  # disconnect packet
    assert src.payloads_ingested == 2
    assert len(k.bus.history) == 0  # no events published, no crash


# ------------------------------------------------------------------ reconnect
def test_feed_reconnects_after_disconnect():
    k = _kernel()
    builds = []
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: builds.append(subs) or FakeFeed(subs))
    src.start()
    before = len(builds)
    src._on_error(None, RuntimeError("ws dropped"))  # dhanhq 2-arg signature
    assert src._reconnect_called
    assert len(builds) > before      # the feed was actually rebuilt
    assert src.running                # and is running again


def test_reconnect_publishes_disconnect_and_resubscribes():
    from ntrade.events.lifecycle import FeedDisconnectedEvent
    from ntrade.domain.market.stream import SubscriptionState
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: FakeFeed(subs))
    src.start()
    inst = k.ctx.instrument("RELIANCE")
    src._on_error(None, RuntimeError("ws closed"))
    assert any(isinstance(e, FeedDisconnectedEvent) for e in k.bus.history)
    assert inst._stream.state is SubscriptionState.SUBSCRIBED
    assert inst._stream.is_subscribed is True
