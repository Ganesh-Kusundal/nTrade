"""Live candle stream — ``/ws/market`` + :class:`LiveCandlePump`.

With ``--provider dhan`` the pump subscribes to Dhan's MarketFeed websocket
and forms in-progress candles from real LTP/LTQ only. There is no synthetic
walk: without a real feed ``subscribe()`` returns ``off`` and the pump never
fabricates a candle. A watchdog emits ``live_status: stale`` when an open
sub stops receiving ticks, and the feed is rebuilt on disconnect.

Bars are anchored to the exchange session open (NSE 09:15 / MCX 09:00 IST).
Streaming is gated by :mod:`ntrade.domain.market_hours`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ntrade.domain.market_hours import IST, is_market_open, session_open
from ntrade.domain.timeframes import interval_span_minutes as _interval_span_minutes, WIRE_INTERVALS
from api.marketdata import MarketDataService

log = logging.getLogger("api.live")

ws_router = APIRouter()

_TICK_S = 1.0
_MAX_SUBS = 64
# Backwards-compat alias — new code should import interval_span_minutes from domain.timeframes.
_SPAN_MIN = {iv: _interval_span_minutes(iv) for iv in WIRE_INTERVALS}


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
        self._stopped = False  # <-- added: prevents reconnect after pump.stop()
        self._feed = None
        self._sec_map: dict = {}
        self._tick_listeners: list[Callable[[str, str, float, int, datetime], None]] = []
        self._quote_listeners: list = []
        self._desired_wires: list = []
        # Feed liveness — MarketFeed can sit in CLOSE_WAIT with no on_close.
        # Subscribe must restart when no payload arrives for this long (s).
        self._last_feed_msg_at: float | None = None
        self._feed_started_at: float | None = None
        self._feed_error: str | None = None

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

    def on_tick(self, fn: Callable[[str, str, float, int, datetime], None]) -> Callable[[], None]:
        """Register a callback invoked on every real tick; returns unsubscribe."""
        self._tick_listeners.append(fn)
        return lambda: self._tick_listeners.remove(fn)

    def on_quote(self, fn) -> Callable[[], None]:
        """Register a callback invoked on every real quote; returns unsubscribe."""
        self._quote_listeners.append(fn)
        return lambda: self._quote_listeners.remove(fn)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._stopped = True  # <-- added
        self._close_feed()

    # ------------------------------------------------------------ clients
    def add_client(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def remove_client(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    # ------------------------------------------------------------ subs
    def subscribe(self, symbol: str, exchange: str, interval: str,
                   strategy: str | None = None, strategy_params: dict | None = None,
                   tick_size: float | None = None) -> dict:
        symbol = symbol.strip().upper()
        exchange = str(exchange or "NFO").upper()
        if not self.enabled:
            return {"symbol": symbol, "exchange": exchange, "interval": interval,
                    "status": "off", "source": self._service.name,
                    "reason": "live streaming disabled"}
        if not self._real_feed:
            return {"symbol": symbol, "exchange": exchange, "interval": interval,
                    "status": "off", "source": self._service.name,
                    "reason": f"no real live feed (provider={self._service.name})"}
        if symbol not in self._subs and len(self._subs) >= _MAX_SUBS:
            raise ValueError(f"subscription limit ({_MAX_SUBS}) reached")
        state = self._subs.get(symbol)
        if state is None:
            state = self._new_state(symbol, exchange, interval)
            self._subs[symbol] = state
        state["exchange"] = exchange
        state["interval"] = interval
        state["span_s"] = _SPAN_MIN.get(interval, 1) * 60
        # Live overlay context — same pipeline the chart + paper use.
        state["strategy"] = strategy
        state["strategy_params"] = strategy_params
        state["tick_size"] = tick_size
        feed_ok = True
        if self._real_feed and self._service.name == "dhan":
            feed_ok = self._ensure_dhan_sub(symbol, exchange)
        now = self._clock()
        open_ = is_market_open(exchange, now)
        if not open_:
            status, reason = "off", f"{exchange} market closed"
        elif not feed_ok:
            status, reason = "off", self._feed_error or "dhan feed not connected"
        else:
            status, reason = "streaming", None
        out = {
            "symbol": symbol, "exchange": exchange, "interval": interval,
            "status": status, "source": self._service.name,
        }
        if reason:
            out["reason"] = reason
        return out

    def unsubscribe(self, symbol: str) -> bool:
        return self._subs.pop(symbol.strip().upper(), None) is not None

    def _new_state(self, symbol: str, exchange: str, interval: str) -> dict:
        return {
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "span_s": _SPAN_MIN.get(interval, 1) * 60,
            "bar_start": None,
            "bar": None,
            "last_tick": None,
            "stale_emitted": False,
            "feed_stale": False,
        }

    # ------------------------------------------------------------ pump loop
    _STALE_S = 5.0

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._tick_s)
            if not self._real_feed:
                continue
            await self._run_once(self._clock())

    async def _run_once(self, now: datetime) -> None:
        for symbol, state in list(self._subs.items()):
            # Session edge: persist + overlay the in-progress bar now — the
            # next bar's first tick never comes after close, so waiting for
            # it (the old behavior) dropped the session's final bar.
            if state.get("bar") is not None and (
                    not is_market_open(state["exchange"], now)
                    or state.get("last_tick") is None):
                completed, state["bar"], state["bar_start"] = state["bar"], None, None
                self._persist_bar(state, completed)
                self._emit_overlays(state, completed)
            if not is_market_open(state["exchange"], now):
                continue
            last = state.get("last_tick")
            if last is None:
                continue
            age = (now - last).total_seconds()
            if age > self._STALE_S and not state.get("stale_emitted"):
                state["stale_emitted"] = True
                state["feed_stale"] = True
                await self._broadcast({"type": "live_status", "symbol": symbol,
                                       "exchange": state["exchange"], "status": "stale",
                                       "source": self._service.name,
                                       "reason": f"no ticks for {int(age)}s"})
            elif age <= self._STALE_S and state.get("stale_emitted"):
                state["stale_emitted"] = False
                state["feed_stale"] = False
                await self._broadcast({"type": "live_status", "symbol": symbol,
                                       "exchange": state["exchange"], "status": "streaming",
                                       "source": self._service.name})

    def ingest_tick(self, symbol: str, price: float, quantity: int = 0,
                    now: datetime | None = None) -> dict | None:
        """Apply a real LTP print to the in-progress bar and broadcast it."""
        state = self._subs.get(symbol.strip().upper())
        if state is None:
            return None
        now = now or self._clock()
        state["last_tick"] = now
        state["stale_emitted"] = False
        self._record_tick(state["symbol"], float(price), int(quantity or 0), now)
        msg = self._apply_tick(state, float(price), int(quantity or 0), now)
        if msg is not None:
            self._emit(msg)
        for fn in self._tick_listeners:
            try:
                fn(state["symbol"], state["exchange"], float(price),
                   int(quantity or 0), now)
            except Exception:  # noqa: BLE001 — a listener never breaks ingestion
                log.exception("tick listener failed")
        return msg

    def _record_tick(self, symbol: str, price: float, qty: int, ts: datetime) -> None:
        if not self._real_feed:
            return
        day = ts.strftime("%Y-%m-%d")
        base = Path(os.environ.get("NTRADE_TICKS_DIR", "data/ticks"))
        path = base / symbol / f"{day}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as fh:
                fh.write(json.dumps(
                    {"ts": ts.isoformat(), "symbol": symbol,
                     "price": price, "qty": qty}) + "\n")
        except Exception:  # noqa: BLE001 — recording must never break ingestion
            log.exception("tick record failed")

    def _persist_bar(self, state: dict, bar: dict) -> None:
        if not self._real_feed:
            return
        try:
            from api.marketdata import parquet_frame
            from ntrade.data.parquet_store import ParquetStorage
            base = os.environ.get("NTRADE_DATA_DIR", "data/ohlcv")
            ParquetStorage(base).upsert(parquet_frame(
                symbol=state["symbol"], exchange=state["exchange"],
                interval=state["interval"], kind="live",
                rows=[{"time": bar["time"], "open": bar["open"], "high": bar["high"],
                       "low": bar["low"], "close": bar["close"], "volume": bar["volume"]}]))
        except Exception:  # noqa: BLE001 — persistence never breaks streaming
            log.exception("live bar persist failed for %s", state["symbol"])

    def _apply_tick(self, state: dict, price: float, qty: int, now: datetime) -> dict | None:
        if price <= 0 or not is_market_open(state["exchange"], now):
            return None
        span_s = state["span_s"]
        open_t = session_open(state["exchange"])
        session_start = datetime.combine(now.date(), open_t, tzinfo=IST)
        elapsed = max((now - session_start).total_seconds(), 0.0)
        bar_start = int(session_start.timestamp()) + (int(elapsed) // span_s) * span_s
        # P0-2: reject out-of-order ticks — if this tick's bar window is before
        # the current bar's start, it's stale and would corrupt OHLCV
        prev_bar_start = state.get("bar_start")
        if prev_bar_start is not None and bar_start < prev_bar_start:
            return None
        bar = state["bar"]
        if bar is None or bar_start != prev_bar_start:
            completed = state["bar"]
            bar = {"time": bar_start, "open": price, "high": price,
                   "low": price, "close": price, "volume": 0}
            state["bar_start"] = bar_start
            state["bar"] = bar
            if completed is not None:
                self._persist_bar(state, completed)
                self._emit_overlays(state, completed)
        bar["high"] = max(bar["high"], price)
        bar["low"] = min(bar["low"], price)
        bar["close"] = price
        bar["volume"] = int(bar.get("volume", 0)) + max(qty, 0)
        return {
            "type": "candle",
            "symbol": state["symbol"],
            "exchange": state["exchange"],
            "interval": state["interval"],
            "candle": {k: round(v, 2) if isinstance(v, float) else v for k, v in bar.items()},
            "ts": now.isoformat(),
        }

    # ponytail: overlay recompute is bounded to completed-bar events (1m ->
    # once/min/symbol). If many symbols subscribe, raise _OVERLAY_LIMIT or
    # throttle to <=1 Hz; the window fetch (last N candles) is O(N) parquet.
    _OVERLAY_WINDOW = 240

    def _emit_overlays(self, state: dict, completed_bar: dict) -> None:
        """Recompute overlays for a symbol after a candle closes and broadcast.

        Reads the trailing window from the parquet store (append the just-
        closed bar so it's included), runs the OverlayPipeline, and emits a
        single ``overlays`` message. Cheap, gated to bar closes only.
        """
        try:
            from ntrade.analytics.overlay_pipeline import build_overlays
            candles = self._service.candles(
                symbol=state["symbol"], exchange=state["exchange"],
                interval=state["interval"], limit=self._OVERLAY_WINDOW)
            candles = candles + [{
                "time": int(completed_bar["time"]),
                "open": float(completed_bar["open"]),
                "high": float(completed_bar["high"]),
                "low": float(completed_bar["low"]),
                "close": float(completed_bar["close"]),
                "volume": float(completed_bar.get("volume", 0)),
            }]
            payload = build_overlays(
                candles, symbol=state["symbol"], exchange=state["exchange"],
                interval=state["interval"], strategy_id=state.get("strategy"),
                strategy_params=state.get("strategy_params"),
                tick_size=state.get("tick_size"),
            ).to_dict()
            self._emit({
                "type": "overlays",
                "symbol": state["symbol"],
                "exchange": state["exchange"],
                "interval": state["interval"],
                "overlays": payload["overlays"],
                "strategy": payload["strategy"],
                "ts": completed_bar.get("time"),
            })
        except Exception:  # noqa: BLE001 — overlays are best-effort live extras
            log.exception("live overlay recompute failed for %s", state["symbol"])

    def _emit(self, msg: dict) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), loop)

    # CLOSE_WAIT zombie feeds produce no on_close; restart if silent this long.
    _FEED_SILENT_RESTART_S = 30.0

    def _feed_needs_restart(self) -> bool:
        import time as _time
        if self._feed is None:
            return True
        now = _time.time()
        last = self._last_feed_msg_at
        if last is not None:
            return (now - last) > self._FEED_SILENT_RESTART_S
        started = self._feed_started_at
        return started is not None and (now - started) > self._FEED_SILENT_RESTART_S

    def _close_feed(self) -> None:
        feed = self._feed
        self._feed = None
        self._feed_started_at = None
        if feed is None:
            return
        try:
            feed.close_connection()
        except Exception:  # noqa: BLE001 — best-effort teardown of a zombie
            pass

    # TODO(api slim): subscription wiring here (lines 380-396: sec_map,
    # _segment/_quote_mode/_full_mode, _desired_wires) duplicates
    # ntrade.sources.dhan_feed.DhanMarketFeedSource._subscriptions (157-172).
    # Clean delegation would be to use DhanMarketFeedSource as the feed
    # owner and remove this hand-rolled MarketFeed wiring. Kept as-is for
    # now — risky deletion would break live pump lifecycle/reconnect —
    # prefer a follow-up that delegates to DhanMarketFeedSource cleanly.
    def _ensure_dhan_sub(self, symbol: str, exchange: str) -> bool:
        """Subscribe ``symbol`` on the Dhan MarketFeed. Returns True if the
        feed object is up after this call (not a guarantee of ticks yet)."""
        contract = self._service.master.resolve(symbol)
        if contract is None or not contract.security_id:
            self._feed_error = f"no security_id for {symbol}"
            log.warning("%s — live ticks skipped", self._feed_error)
            return False
        sec = int(contract.security_id)
        self._sec_map[sec] = (symbol, exchange)
        self._sec_map[str(sec)] = (symbol, exchange)
        wire = (_segment(contract.exchange), str(sec),
                _quote_mode() if contract.exchange == "INDEX" else _full_mode())
        if not self._desired_wires:
            self._desired_wires = [wire]
        elif wire not in self._desired_wires:
            self._desired_wires.append(wire)
        try:
            if self._feed_needs_restart():
                # Drop zombie CLOSE_WAIT sockets that never fire on_close.
                if self._feed is not None:
                    log.warning("dhan live feed silent — forcing restart for %s", symbol)
                self._close_feed()
                # dhanhq MarketFeed.__init__ calls set_event_loop — must not
                # run on the FastAPI asyncio thread.
                err: list[BaseException] = []
                def boot():
                    try:
                        self._start_dhan_feed(list(self._desired_wires))
                    except BaseException as exc:
                        err.append(exc)
                t = threading.Thread(target=boot, daemon=True)
                t.start()
                t.join(timeout=8)
                if err:
                    raise err[0]
                if self._feed is None:
                    raise RuntimeError("MarketFeed boot timed out")
            else:
                self._feed.subscribe_symbols([wire])
            self._feed_error = None
            return True
        except Exception as exc:
            self._feed_error = str(exc)
            self._close_feed()
            log.warning("dhan live feed subscribe failed for %s: %s", symbol, exc)
            return False

    def _start_dhan_feed(self, instruments: list) -> None:
        import time as _time
        from dhanhq import DhanContext, MarketFeed
        client_id, token = self._service.provider.feed_auth()
        self._feed = MarketFeed(
            DhanContext(client_id, token), instruments, version="v2",
            on_message=self._on_dhan_message,
            on_error=self._on_feed_closed,
            on_close=self._on_feed_closed,
        )
        self._feed_started_at = _time.time()
        self._last_feed_msg_at = None
        self._feed.start()

    def _on_dhan_message(self, _instance, payload) -> None:
        import time as _time
        from ntrade.events.market import TickEvent, QuoteEvent
        from ntrade.sources.dhan_feed import dhan_payload_to_events

        self._last_feed_msg_at = _time.time()
        for event in dhan_payload_to_events(payload, self._sec_map, self._clock()):
            if isinstance(event, TickEvent) and event.price > 0:
                self.ingest_tick(event.symbol, event.price, event.quantity)
            elif isinstance(event, QuoteEvent):
                for fn in self._quote_listeners:
                    try:
                        fn(event)
                    except Exception:  # noqa: BLE001
                        log.exception("quote listener failed")

    def _on_feed_closed(self, _f=None, _err=None) -> None:
        log.warning("dhan live feed lost — scheduling reconnect (%s)", _err)
        self._feed = None
        self._feed_started_at = None
        self._feed_error = f"feed closed: {_err}" if _err else "feed closed"
        if self._stopped:  # <-- added: pump was explicitly stopped
            return
        if not self._desired_wires:
            return
        threading.Thread(target=self._reconnect, daemon=True).start()

    def _reconnect(self) -> None:
        import time as _time
        if self._stopped:  # <-- added: pump was explicitly stopped
            return
        # Keep trying — a 50s give-up left the UI claiming "streaming" with
        # a dead CLOSE_WAIT socket until process restart (observed 2026-08-14).
        delay = 2.0
        while not self._stopped:
            if self._feed is not None:
                return
            try:
                self._start_dhan_feed(list(self._desired_wires))
                self._feed_error = None
                return
            except Exception as exc:  # noqa: BLE001
                self._feed_error = str(exc)
                log.warning("dhan feed reconnect failed: %s", exc)
            _time.sleep(delay)
            delay = min(delay * 1.5, 30.0)

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
                        strategy=msg.get("strategy"),
                        strategy_params=msg.get("strategy_params"),
                        tick_size=msg.get("tick_size"),
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


def _quote_mode() -> int:
    from dhanhq import MarketFeed
    return int(getattr(MarketFeed, "Quote", 17))
