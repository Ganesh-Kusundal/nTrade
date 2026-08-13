"""Live candle stream — ``/ws/market`` + :class:`LiveCandlePump`.

With ``--provider dhan`` the pump subscribes to Dhan's MarketFeed websocket
and forms in-progress candles from real LTP/LTQ. Synthetic/parquet keep the
1Hz random walk for offline demos.

Bars are anchored to the exchange session open (NSE 09:15 / MCX 09:00 IST).
Streaming is gated by :mod:`api.market_hours`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import threading
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ntrade.domain.market_hours import IST, is_market_open, session_open
from api.marketdata import MarketDataService

log = logging.getLogger("api.live")

ws_router = APIRouter()

_TICK_S = 1.0
_MAX_SUBS = 64
_SPAN_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1D": 375}


class LiveCandlePump:
    """Per-symbol live candles broadcast to connected clients.

    ``enabled=False`` forces historical-only (no pump task). With
    ``enabled=True`` (default for the app), subscriptions still go ``off``
    outside that exchange's session — see :func:`is_market_open`.
    """

    def __init__(self, service: MarketDataService, tick_s: float = _TICK_S,
                 enabled: bool = True,
                 clock: Callable[[], datetime] | None = None):
        self._service = service
        self._tick_s = tick_s
        self.enabled = enabled
        self._clock = clock or (lambda: datetime.now(tz=IST))
        self._clients: set[WebSocket] = set()
        self._subs: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # True when live bars must come from broker ticks (dhan), not RNG.
        self._real_feed = False
        self._feed = None
        self._sec_map: dict = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.enabled:
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def attach_broker_feed(self) -> None:
        """Form live candles from Dhan MarketFeed ticks instead of the RNG walk."""
        self._real_feed = True

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        feed = self._feed
        self._feed = None
        if feed is not None:
            try:
                feed.close_connection()
            except Exception:
                pass

    # ------------------------------------------------------------ clients
    def add_client(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def remove_client(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    # ------------------------------------------------------------ subs
    def subscribe(self, symbol: str, exchange: str, interval: str) -> dict:
        symbol = symbol.strip().upper()
        exchange = str(exchange or "NFO").upper()
        if not self.enabled:
            return {"symbol": symbol, "exchange": exchange, "interval": interval,
                    "status": "off", "source": self._service.name,
                    "reason": "live streaming disabled"}
        if symbol not in self._subs and len(self._subs) >= _MAX_SUBS:
            raise ValueError(f"subscription limit ({_MAX_SUBS}) reached")
        state = self._subs.get(symbol)
        if state is None:
            state = self._new_state(symbol, exchange, interval)
            self._subs[symbol] = state
        state["exchange"] = exchange
        state["interval"] = interval
        state["span_s"] = _SPAN_MIN.get(interval, 1) * 60
        if self._real_feed and self._service.name == "dhan":
            self._ensure_dhan_sub(symbol, exchange)
        now = self._clock()
        open_ = is_market_open(exchange, now)
        return {
            "symbol": symbol, "exchange": exchange, "interval": interval,
            "status": "streaming" if open_ else "off",
            "source": self._service.name,
            **({} if open_ else {"reason": f"{exchange} market closed"}),
        }

    def unsubscribe(self, symbol: str) -> bool:
        return self._subs.pop(symbol.strip().upper(), None) is not None

    def _new_state(self, symbol: str, exchange: str, interval: str) -> dict:
        span_s = _SPAN_MIN.get(interval, 1) * 60
        # Synthetic walk seeds from last close. Broker feed waits for the
        # first real tick — do not pull 90d of history just to seed a price.
        price = 0.0
        rng = None
        if not self._real_feed:
            last = self._service.candles(symbol=symbol, exchange=exchange,
                                         interval=interval, limit=1)
            price = float(last[-1]["close"]) if last else self._base(symbol)
            rng = random.Random(_seed(symbol, interval))
        return {
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "span_s": span_s,
            "price": price,
            "rng": rng,
            "bar_start": None,
            "bar": None,
        }

    @staticmethod
    def _base(symbol: str) -> float:
        digest = int.from_bytes(hashlib.md5(symbol.encode()).digest()[:8], "big")
        return round(15_000.0 + (digest % 55_000) / 10.0, 2)

    # ------------------------------------------------------------ pump loop
    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._tick_s)
            if self._real_feed or not self._subs or not self._clients:
                continue
            now = self._clock()
            for state in list(self._subs.values()):
                msg = self._tick(state, now)
                if msg is not None:
                    await self._broadcast(msg)

    def ingest_tick(self, symbol: str, price: float, quantity: int = 0,
                    now: datetime | None = None) -> dict | None:
        """Apply a real LTP print to the in-progress bar and broadcast it."""
        state = self._subs.get(symbol.strip().upper())
        if state is None:
            return None
        msg = self._apply_tick(state, float(price), int(quantity or 0),
                               now or self._clock())
        if msg is not None:
            self._emit(msg)
        return msg

    def _tick(self, state: dict, now: datetime) -> dict | None:
        """Synthetic 1Hz walk (offline providers only)."""
        rng = state.get("rng")
        if rng is None:
            return None
        price = max(state["price"] * (1.0 + rng.uniform(-0.0006, 0.0006)), 0.01)
        return self._apply_tick(state, price, rng.randint(1, 25), now)

    def _apply_tick(self, state: dict, price: float, qty: int, now: datetime) -> dict | None:
        if price <= 0 or not is_market_open(state["exchange"], now):
            return None
        span_s = state["span_s"]
        open_t = session_open(state["exchange"])
        session_start = datetime.combine(now.date(), open_t, tzinfo=IST)
        elapsed = max((now - session_start).total_seconds(), 0.0)
        bar_start = int(session_start.timestamp()) + (int(elapsed) // span_s) * span_s
        bar = state["bar"]
        if bar is None or bar_start != state["bar_start"]:
            bar = {"time": bar_start, "open": price, "high": price,
                   "low": price, "close": price, "volume": 0}
            state["bar_start"] = bar_start
            state["bar"] = bar
        bar["high"] = max(bar["high"], price)
        bar["low"] = min(bar["low"], price)
        bar["close"] = price
        bar["volume"] = int(bar.get("volume", 0)) + max(qty, 0)
        state["price"] = price
        return {
            "type": "candle",
            "symbol": state["symbol"],
            "exchange": state["exchange"],
            "interval": state["interval"],
            "candle": {k: round(v, 2) if isinstance(v, float) else v for k, v in bar.items()},
            "ts": now.isoformat(),
        }

    def _emit(self, msg: dict) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), loop)

    def _ensure_dhan_sub(self, symbol: str, exchange: str) -> None:
        contract = self._service.master.resolve(symbol)
        if contract is None or not contract.security_id:
            log.warning("no security_id for %s — live ticks skipped", symbol)
            return
        sec = int(contract.security_id)
        self._sec_map[sec] = (symbol, exchange)
        self._sec_map[str(sec)] = (symbol, exchange)
        wire = (_segment(contract.exchange), str(sec), _full_mode())
        try:
            if self._feed is None:
                # dhanhq MarketFeed.__init__ calls set_event_loop — must not
                # run on the FastAPI asyncio thread.
                err: list[BaseException] = []
                def boot():
                    try:
                        self._start_dhan_feed([wire])
                    except BaseException as exc:
                        err.append(exc)
                t = threading.Thread(target=boot, daemon=True)
                t.start()
                t.join(timeout=5)
                if err:
                    raise err[0]
            else:
                self._feed.subscribe_symbols([wire])
        except Exception as exc:
            log.warning("dhan live feed subscribe failed for %s: %s", symbol, exc)

    def _start_dhan_feed(self, instruments: list) -> None:
        from dhanhq import DhanContext, MarketFeed
        client_id, token = self._service.provider.feed_auth()
        self._feed = MarketFeed(
            DhanContext(client_id, token), instruments, version="v2",
            on_message=self._on_dhan_message,
            on_error=lambda _f, err: log.error("dhan feed error: %s", err),
            on_close=lambda _f: log.warning("dhan feed closed"),
        )
        self._feed.start()

    def _on_dhan_message(self, _instance, payload) -> None:
        from ntrade.events.market import TickEvent
        from ntrade.sources.dhan_feed import dhan_payload_to_events

        for event in dhan_payload_to_events(payload, self._sec_map, self._clock()):
            if isinstance(event, TickEvent) and event.price > 0:
                self.ingest_tick(event.symbol, event.price, event.quantity)

    async def _broadcast(self, msg: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_client(ws)


# ------------------------------------------------------------------- endpoint


@ws_router.websocket("/ws/market")
async def ws_market(ws: WebSocket) -> None:
    pump: LiveCandlePump = ws.app.state.pump
    await ws.accept()
    pump.add_client(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                await ws.send_json({"type": "error", "detail": "invalid JSON"})
                continue
            mtype = msg.get("type")
            if mtype == "subscribe":
                symbol = str(msg.get("symbol", "")).strip().upper()
                if not symbol:
                    await ws.send_json({"type": "error", "detail": "symbol required"})
                    continue
                try:
                    resp = pump.subscribe(
                        symbol,
                        str(msg.get("exchange", "NFO")).upper(),
                        str(msg.get("interval", "1m")),
                    )
                except ValueError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                    continue
                await ws.send_json({"type": "live_status", **resp})
            elif mtype == "unsubscribe":
                pump.unsubscribe(str(msg.get("symbol", "")).upper())
            elif mtype == "ping":
                await ws.send_json({"type": "pong"})
            else:
                await ws.send_json({"type": "error",
                                    "detail": f"unknown message type {mtype!r}"})
    except WebSocketDisconnect:
        pass
    finally:
        pump.remove_client(ws)


def _seed(*parts: str) -> int:
    digest = hashlib.md5("|".join(parts).encode()).digest()[:8]
    return int.from_bytes(digest, "big")


def _segment(exchange: str) -> int:
    from dhanhq import MarketFeed
    e = (exchange or "").upper()
    if e == "MCX":
        return int(MarketFeed.MCX)
    if e in ("NFO", "NSE_FNO"):
        return int(MarketFeed.NSE_FNO)
    if e in ("BFO", "BSE_FNO"):
        return int(MarketFeed.BSE_FNO)
    if e == "INDEX":
        return int(MarketFeed.IDX)
    return int(MarketFeed.NSE)


def _full_mode() -> int:
    from dhanhq import MarketFeed
    return int(getattr(MarketFeed, "Full", 21))
