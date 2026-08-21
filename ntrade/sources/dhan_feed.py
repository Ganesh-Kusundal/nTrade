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

import threading
import time
import logging

from ntrade.events.market import DepthEvent, QuoteEvent, TickEvent
from ntrade.sources.market_feed import MarketFeedSource
from ntrade.events.lifecycle import FeedDisconnectedEvent
from ntrade.execution.retry import RateLimiter
from ntrade.domain.coercion import to_float, to_int

# Guarded import for the wire constants only — dhanhq ships with
# Dhan-Tradehull, but the module must stay importable without it (the feed
# itself is still built lazily in ``_build_feed``). Falls back to the stable
# documented values when the package is absent.
try:
    from dhanhq import MarketFeed as _MarketFeed
except ImportError:  # pragma: no cover - dhanhq ships with Dhan-Tradehull
    _MarketFeed = None

_IDX_SEGMENT = int(getattr(_MarketFeed, "IDX", 0))
_FULL_MODE = int(getattr(_MarketFeed, "Full", 21))
_QUOTE_MODE = int(getattr(_MarketFeed, "Quote", 17))

_logger = logging.getLogger("ntrade.feed.dhan")


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
            quantity=to_int(payload.get("LTQ")), ts=ts,
        ))

    if ptype in ("quote data", "full data"):
        events.append(QuoteEvent(
            symbol=symbol, exchange=exchange, ltp=ltp,
            open=to_float(payload.get("open")), high=to_float(payload.get("high")),
            low=to_float(payload.get("low")), prev_close=to_float(payload.get("close")),
            volume=to_int(payload.get("volume")), oi=to_int(payload.get("OI")), ts=ts,
        ))

    depth = payload.get("depth") or ()
    bids, asks = [], []
    for level in depth:
        if not isinstance(level, dict):
            continue
        bid_px = to_float(level.get("bid_price"))
        ask_px = to_float(level.get("ask_price"))
        if bid_px > 0:
            bids.append((bid_px, to_int(level.get("bid_quantity")),
                         to_int(level.get("bid_orders"))))
        if ask_px > 0:
            asks.append((ask_px, to_int(level.get("ask_quantity")),
                         to_int(level.get("ask_orders"))))
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
        # P0-1 dedup: (symbol, exchange) → (epoch_s, price, qty) for duplicate ticks
        self._dedup_window: dict[tuple[str, str], tuple[float, float, int]] = {}
        self._dedup_ttl_s: float = 1.0
        # M-2: stop() sets this so the websocket's close/error callbacks know
        # the shutdown was deliberate and must not trigger a reconnect.
        self._intentional_stop = False
        # D-020: serializes start()/stop()/_reconnect() so a LiveRunner stop
        # racing the dhanhq error/close callbacks cannot double-close or
        # rebuild the single-use websocket concurrently. RLock: _reconnect
        # re-enters via stop()/start().
        self._lifecycle_lock = threading.RLock()

    # ------------------------------------------------------------------ wiring
    def _subscriptions(self) -> list:
        """Wire subscription tuples: ``(exchange_segment, security_id, mode)``.

        dhanhq v2 JSON requires SecurityId as a string — int IDs connect
        but deliver zero ticks with no error (silent empty feed).

        Mode is picked per segment: Dhan's IDX (index) segment silently
        drops Full(21) subscriptions — it accepts them but never delivers
        data (verified live 2026-08-06). Equities/F&O/others stream fine on
        Full(21) (quote+tick+depth); indices use Quote(17) (quote+tick),
        which the server does serve and maps through ``dhan_payload_to_events``.
        """
        return [
            (exch, str(sec), _QUOTE_MODE if exch == _IDX_SEGMENT else _FULL_MODE)
            for exch, sec in self.symbols
        ]

    def _build_feed(self):
        if self._feed is not None:
            return self._feed
        if self.feed_factory is not None:
            self._feed = self.feed_factory(self._subscriptions())
            return self._feed
        try:
            from dhanhq import MarketFeed
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

        # Re-use already authenticated tsl from kernel context if available (P4)
        if self.kernel is not None:
            for inst in self.kernel.ctx.instruments_snapshot():
                broker = getattr(inst, "broker_adapter", None)
                tsl = getattr(broker, "tsl", None) if broker else None
                if tsl is not None and getattr(tsl, "ClientCode", None) and getattr(tsl, "token_id", None):
                    return DhanContext(tsl.ClientCode, tsl.token_id)

        tsl = get_tradehull(gate=self._gate)
        return DhanContext(tsl.ClientCode, tsl.token_id)

    # ------------------------------------------------------------------ feed
    def start(self) -> None:
        """Start the websocket in a background thread (non-blocking)."""
        with self._lifecycle_lock:
            self._intentional_stop = False
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
        with self._lifecycle_lock:
            self._intentional_stop = True
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
        events = dhan_payload_to_events(payload, self.symbol_map, ts)
        # P0-1: content-dedup ticks within the sliding window (broker replay / gateway retry)
        events = self._dedupe_ticks(events)
        for event in events:
            self.bus.publish(event)
        self.payloads_ingested += 1

    def _dedupe_ticks(self, events: list) -> list:
        """Drop duplicate ticks — Dhan MarketFeed may replay the same (price, qty)
        within a short window. A duplicate tick inflates volume, corrupts OHLCV,
        and can trigger phantom signals. Returns events with dups removed."""
        if not events:
            return events
        out = []
        for event in events:
            if isinstance(event, TickEvent):
                key = (event.symbol, event.exchange)
                ts_s = event.ts.timestamp()
                last = self._dedup_window.get(key)
                if last and (ts_s - last[0]) < self._dedup_ttl_s \
                        and last[1] == event.price and last[2] == event.quantity:
                    continue  # duplicate — skip
                self._dedup_window[key] = (ts_s, event.price, event.quantity)
            out.append(event)
        return out

    def _on_error(self, instance, error) -> None:
        _logger.error("feed error: %s", error)
        if self.kernel is not None:
            self.bus.publish(FeedDisconnectedEvent(
                reason=str(error), ts=self.kernel.clock.now()))
        if not self._intentional_stop:
            self._reconnect()

    # Reconnect re-uses start()/stop(), so code-21 + version v2 are always
    # reapplied at re-arm (see tests/test_contract_feed_reconnect_subscription.py)
    def _reconnect(self) -> None:
        with self._lifecycle_lock:
            if self._intentional_stop:
                return  # deliberate shutdown — never rebuild the socket
            self._reconnect_limiter.wait()
            try:
                self.stop()          # tear down the dead socket
                self._intentional_stop = False  # stop() armed it; start() below is wanted
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
        # M-2: an unexpected close (exchange/broker-side drop) must reconnect;
        # only a deliberate stop() leaves the feed down.
        if not self._intentional_stop:
            self._reconnect()

    @property
    def running(self) -> bool:
        return getattr(self, "_running", False)

    @running.setter
    def running(self, value: bool) -> None:
        self._running = bool(value)
