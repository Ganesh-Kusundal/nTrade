"""DhanMarketFeedSource — the live Dhan websocket as a pure event source.

Thin adapter over ``dhanhq.MarketFeed`` (a dependency of Dhan-Tradehull): the
kernel never sees the websocket. Each parsed payload dict is mapped to
canonical events by the pure function ``dhan_payload_to_events`` and published
to the kernel bus. The mapper is fully unit-tested offline with sample
payloads; only the live websocket connection itself needs market credentials
(kept as a thin adapter so the kernel stays untouched — the zero-parity
invariant).
"""

from __future__ import annotations

import time
import logging

from ntrade.events.market import DepthEvent, QuoteEvent, TickEvent
from ntrade.sources.market_feed import MarketFeedSource
from ntrade.events.lifecycle import FeedDisconnectedEvent
from ntrade.execution.retry import RateLimiter

_logger = logging.getLogger("ntrade.feed.dhan")


def _to_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def dhan_payload_to_events(payload: dict, symbol_map: dict, ts) -> list:
    """Map a parsed dhanhq MarketFeed payload dict to canonical events.

    ``symbol_map`` maps ``security_id -> (symbol, exchange)``. Unknown
    securities, non-dict payloads (status/disconnect packets) and malformed
    fields are skipped — a bad payload never breaks the kernel. Ticker →
    TickEvent; Quote → QuoteEvent + TickEvent; Full → both + DepthEvent;
    Market Depth → DepthEvent + TickEvent.
    """
    if not isinstance(payload, dict):
        return []  # status strings ("Markets Open") and None (disconnect)
    security_id = payload.get("security_id")
    mapping = symbol_map.get(security_id) or symbol_map.get(str(security_id))
    if mapping is None:
        return []
    symbol, exchange = mapping
    ptype = str(payload.get("type", "")).lower()

    if "LTP" in payload:
        try:
            ltp = float(payload["LTP"])
        except (TypeError, ValueError):
            return []  # malformed price → skip the whole payload, no crash
    else:
        ltp = 0.0
    events: list = []

    if ptype in ("ticker data", "quote data", "full data", "market depth"):
        events.append(TickEvent(
            symbol=symbol, exchange=exchange, price=ltp,
            quantity=_to_int(payload.get("LTQ")), ts=ts,
        ))

    if ptype in ("quote data", "full data"):
        events.append(QuoteEvent(
            symbol=symbol, exchange=exchange, ltp=ltp,
            open=_to_float(payload.get("open")), high=_to_float(payload.get("high")),
            low=_to_float(payload.get("low")), prev_close=_to_float(payload.get("close")),
            volume=_to_int(payload.get("volume")), oi=_to_int(payload.get("OI")), ts=ts,
        ))

    depth = payload.get("depth") or ()
    bids, asks = [], []
    for level in depth:
        if not isinstance(level, dict):
            continue
        bid_px = _to_float(level.get("bid_price"))
        ask_px = _to_float(level.get("ask_price"))
        if bid_px > 0:
            bids.append((bid_px, _to_int(level.get("bid_quantity")),
                         _to_int(level.get("bid_orders"))))
        if ask_px > 0:
            asks.append((ask_px, _to_int(level.get("ask_quantity")),
                         _to_int(level.get("ask_orders"))))
    if bids or asks:
        events.append(DepthEvent(symbol=symbol, exchange=exchange,
                                 bids=tuple(bids), asks=tuple(asks), ts=ts))
    return events


class DhanMarketFeedSource(MarketFeedSource):
    """Live Dhan websocket feed wrapped as a ``MarketFeedSource``.

    ``symbols`` are the wire subscriptions ``(exchange_code, security_id)``
    (exchange codes: ``dhanhq.MarketFeed.NSE`` etc.); ``symbol_map`` names the
    same security_ids as ``(symbol, exchange)`` for canonical events. Inject a
    ``feed_factory`` for offline tests; otherwise a real ``dhanhq.MarketFeed``
    is built lazily (requires credentials at runtime, not import time).
    """

    name = "dhan"

    def __init__(self, kernel=None, *, symbols: list | None = None,
                 symbol_map: dict | None = None, feed_factory=None,
                 dhan_context=None, gate=None):
        super().__init__(kernel)
        self.symbols = list(symbols or [])
        self.symbol_map = dict(symbol_map or {})
        self.feed_factory = feed_factory
        self.dhan_context = dhan_context
        # Session BrokerRateGate (T-035): the login-time data-plane probe
        # (get_tradehull -> _login_ok) respects Quote/Data quotas instead of
        # bursting at feed construction. None keeps standalone callers safe.
        self._gate = gate
        self._feed = None
        self._thread = None
        self.payloads_ingested = 0
        self._timer = time.monotonic
        self._sleep = time.sleep
        self._reconnect_limiter = RateLimiter(calls_per_second=0.5)
        self._reconnect_called = False

    # ------------------------------------------------------------------ wiring
    def _subscriptions(self) -> list:
        return [(exch, sec, 21) for exch, sec in self.symbols]

    def _build_feed(self):
        if self._feed is not None:
            return self._feed
        if self.feed_factory is not None:
            self._feed = self.feed_factory(self._subscriptions())
            return self._feed
        try:
            from dhanhq import DhanContext, MarketFeed
        except ImportError as exc:  # pragma: no cover - dhanhq ships with Dhan-Tradehull
            raise ImportError(
                "DhanMarketFeedSource requires dhanhq (installed with Dhan-Tradehull)"
            ) from exc
        context = self.dhan_context or self._context_from_env()
        feed = MarketFeed(
            context, self._subscriptions(), version="v2",
            on_message=self._on_message, on_error=self._on_error,
            on_close=self._on_close,
        )
        self._feed = feed
        return feed

    def _context_from_env(self):
        from dhanhq import DhanContext
        from ntrade.brokers.dhan_auth import get_tradehull

        # T-035: feed construction performs the same _login_ok probes the broker
        # connect path does (B-012) — route them through the session gate too.
        tsl = get_tradehull(gate=self._gate)
        return DhanContext(tsl.ClientCode, tsl.token_id)

    # ------------------------------------------------------------------ feed
    def start(self) -> None:
        """Start the websocket in a background thread (non-blocking)."""
        self._reconnect_limiter.wait()
        feed = self._build_feed()
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = feed.start()
        self.running = True

    def wait_ready(self, timeout: float = 15.0, min_ticks: int = 1) -> bool:
        """Block until the feed is connected and has ingested >= min_ticks
        payloads. Returns False (without raising) when the deadline expires —
        the LiveRunner treats that as a failed warmup and stops."""
        deadline = self._timer() + timeout
        while self._timer() < deadline:
            if self.running and self.payloads_ingested >= min_ticks:
                return True
            self._sleep(0.05)
        return self.running and self.payloads_ingested >= min_ticks

    def stop(self) -> None:
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception:
                pass
        # drop the cached feed so start() builds a fresh one (a dhanhq
        # MarketFeed's event loop is single-use and cannot be restarted).
        self._feed = None
        self._thread = None
        self.running = False

    def _on_message(self, instance, payload: dict) -> None:
        """dhanhq callback: translate a payload to canonical events + publish."""
        if self.kernel is None:
            return
        ts = self.kernel.clock.now()
        for event in dhan_payload_to_events(payload, self.symbol_map, ts):
            self.bus.publish(event)
        self.payloads_ingested += 1

    def _on_error(self, instance, error) -> None:
        _logger.error("feed error: %s", error)
        if self.kernel is not None:
            self.bus.publish(FeedDisconnectedEvent(
                reason=str(error), ts=self.kernel.clock.now()))
        self._reconnect()

    # Reconnect re-uses start()/stop(), so code-21 + version v2 are always
    # reapplied at re-arm (see tests/test_contract_feed_reconnect_subscription.py)
    def _reconnect(self) -> None:
        self._reconnect_limiter.wait()
        try:
            self.stop()          # tear down the dead socket
            self.start()         # re-attach + re-subscribe (fresh MarketFeed)
            self._reconnect_called = True
            # Re-arm every instrument stream so consumers see them as live again.
            if self.kernel is not None:
                for instrument in self.kernel.ctx.instruments_snapshot():
                    stream = getattr(instrument, "_stream", None)
                    if stream is not None:
                        stream.notify_reconnect()
        except Exception as exc:
            _logger.error("reconnect failed — feed remains down: %s", exc)

    def _on_close(self, instance) -> None:
        _logger.warning("feed closed")
        self.running = False
        if self.kernel is not None:
            self.bus.publish(FeedDisconnectedEvent(
                reason="websocket closed", ts=self.kernel.clock.now(),
            ))

    @property
    def running(self) -> bool:
        return getattr(self, "_running", False)

    @running.setter
    def running(self, value: bool) -> None:
        self._running = bool(value)
