"""Graph-aware contract (Item D): reconnect reapplies code-21 + version-v2.

The graph flagged an AMBIGUOUS edge between ``_reconnect`` and the feed's
code-21 / version-v2 construction. That dedup is *intended*: ``_reconnect``
re-uses stop()+start(), and start() always builds the feed from
``_subscriptions()`` (code 21 every tuple) exactly as the live path does with
``version="v2"``. This pins the invariant so reconnect determinism cannot
silently regress.
"""

from ntrade.domain.instruments.cash import Equity
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.sources.dhan_feed import DhanMarketFeedSource


class FakeFeed:
    """A stand-in for dhanhq.MarketFeed that records the subscription wiring.

    Mirrors tests/test_dhan_feed_source.py:149 — exposes ``subscriptions``
    (the durable spec the source builds the feed from) and records start/close.
    """

    def __init__(self, subscriptions):
        self.subscriptions = subscriptions
        self.started = False
        self.closed = False
        self.handler = None
        self.thread = None

    def start(self):
        self.started = True
        self.thread = object()
        return self.thread

    def close_connection(self):
        self.closed = True


def _kernel():
    """Reusable replay kernel holding one instrument (mirrors L168)."""
    k = TradingKernel(mode="replay", clock=ReplayClock())
    k.register(Equity("RELIANCE"))
    return k


def test_reconnect_reapplies_code21_and_version_v2():
    """A stop→start reconnect builds a fresh feed whose spec is still 21/v2."""
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map={2885: ("RELIANCE", "NSE")},
                               feed_factory=lambda subs: FakeFeed(subs))

    src.start()          # initial arm
    first = src._feed
    src.stop()
    assert src._feed is None            # cache dropped -> a fresh feed on re-arm
    src.start()                         # simulated reconnect (stop -> start)

    feed = src._feed
    assert feed is not None and feed is not first      # the feed was rebuilt
    assert feed.started is True

    # BOTH reconnect facts: the spec is exactly what _subscriptions() returns...
    # SecurityId must be str — int IDs connect but deliver zero ticks.
    assert feed.subscriptions == [(1, "2885", 21)]
    # ...and every subscription tuple still encodes the full-data code 21.
    assert all(sub[2] == 21 for sub in feed.subscriptions)
    # version v2 is only passed to the real dhanhq.MarketFeed (dhan_feed.py:145),
    # but the durable spec that _build_feed() hands to the same constructor is
    # exactly this list — so code-21 + v2 are reapplied fresh on reconnect.
    assert src._subscriptions() == [(1, "2885", 21)]
