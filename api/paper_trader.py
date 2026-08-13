"""Paper trading service + REST routes.

Runs ``MorningVAHVAL`` on a ₹1M ``PaperBroker`` session that is fed by the
live candle pump — a UI control ("paper trade on the selected symbol") that
exercises the real kernel pipeline (feed → candles → strategy → risk →
paper fills) with zero real orders.

The pump forms live 1m bars; this service polls the pump's in-progress bar
and feeds each *completed* bar into the paper kernel as Quote +
CandleClosed events (the strategy reacts on candle close, exactly like the
live engine stack). Outside exchange hours the pump produces no bars and the
paper session simply idles.

Routes:
    POST /api/paper/start   {symbol, exchange, lot_size}
    POST /api/paper/stop
    GET  /api/paper/status
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ntrade.domain.market_hours import IST
from ntrade.events.market import CandleClosedEvent, QuoteEvent

log = logging.getLogger("api.paper")

paper_router = APIRouter(prefix="/api/paper", tags=["paper"])

_POLL_S = 1.0


class PaperStartRequest(BaseModel):
    symbol: str
    exchange: str = "NFO"
    lot_size: int | None = None


def _default_lot_size(symbol: str) -> int:
    """Index-futures lot sizes (raw qty multiple the strategy floors to).

    Kept in the service so the UI's start control needs only the symbol.
    """
    up = symbol.upper()
    if "BANKNIFTY" in up:
        return 15
    if up.startswith("NIFTY"):
        return 75
    if up.startswith("SENSEX"):
        return 20
    if "FINNIFTY" in up:
        return 40
    if up.startswith("GOLD"):
        return 1
    return 1


class PaperTraderService:
    """Owns at most one paper session at a time, keyed to a symbol."""

    def __init__(self, market, pump, initial_cash: float = 1_000_000.0):
        self._market = market
        self._pump = pump
        self._initial_cash = float(initial_cash)
        self._lock = threading.Lock()
        self._session = None
        self._symbol: str | None = None
        self._exchange: str = "NFO"
        self._lot_size = 1
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pending_bar = None
        self._last_bar_time = 0
        self._started_at: str | None = None
        self._error: str | None = None
        self._fills: list[dict] = []
        self._signals: list[dict] = []

    # ------------------------------------------------------------------ API
    def start(self, symbol: str, exchange: str = "NFO",
              lot_size: int | None = None) -> dict:
        symbol = symbol.strip().upper()
        with self._lock:
            if self._session is not None:
                already = True
            else:
                already = False
                try:
                    self._build_session(symbol, exchange.upper(), lot_size)
                except Exception as exc:  # noqa: BLE001 — surface to the caller
                    log.exception("paper start failed for %s", symbol)
                    self._error = str(exc)
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                self._started_at = datetime.now(tz=IST).isoformat()
                self._error = None
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._feed_loop, daemon=True,
                                                name="paper-feed")
                self._thread.start()
        # status() takes the same lock — call it outside the with block.
        if not already:
            log.info("paper trading started: %s (%s)", symbol, exchange.upper())
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            session = self._session
            symbol = self._symbol
            self._session = None
            self._symbol = None
            self._stop_event.set()
            if session is not None:
                try:
                    session.kernel.stop(reason="paper stop")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    session.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            thread = self._thread
            self._thread = None
            self._pending_bar = None
            self._last_bar_time = 0
        if thread is not None:
            thread.join(timeout=_POLL_S * 2)
        log.info("paper trading stopped (%s)", symbol)
        return self.status()

    def status(self) -> dict:
        with self._lock:
            session = self._session
            symbol = self._symbol
            started_at = self._started_at
            error = self._error
            fills = list(self._fills)
            signals = list(self._signals)
        if session is None:
            return {
                "running": False,
                "symbol": None,
                "exchange": None,
                "initial_cash": round(self._initial_cash, 2),
                "balance": round(self._initial_cash, 2),
                "equity": round(self._initial_cash, 2),
                "positions": [],
                "trades": [],
                "n_trades": 0,
                "started_at": None,
                "error": error,
            }
        account = session.kernel.ctx.account
        positions = [
            {
                "symbol": p.symbol,
                "quantity": int(p.quantity),
                "avg_price": round(float(p.avg_price or 0.0), 4),
                "ltp": round(float(p.ltp or 0.0), 4),
                "pnl": round(float(getattr(p, "pnl", 0.0) or 0.0), 2),
            }
            for p in session.kernel.ctx.portfolio.positions
        ]
        # Realized PnL from the closed round trips (fills flow), plus unrealized
        # MTM on the open position.
        realized = sum(float(f.get("pnl", 0.0) or 0.0) for f in fills)
        open_pos = session.kernel.ctx.portfolio.position(symbol) if symbol else None
        unrealized = 0.0
        if open_pos is not None:
            unrealized = (float(open_pos.ltp or 0.0) - float(open_pos.avg_price or 0.0)) \
                * int(open_pos.quantity)
        return {
            "running": True,
            "symbol": symbol,
            "exchange": self._exchange,
            "lot_size": self._lot_size,
            "initial_cash": round(self._initial_cash, 2),
            "balance": round(float(account.balance), 2),
            "equity": round(float(account.balance) + unrealized, 2),
            "positions": positions,
            "trades": fills[-20:],
            "n_trades": len(fills),
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "started_at": started_at,
            "error": error,
        }

    # ------------------------------------------------------------- internals
    def _build_session(self, symbol: str, exchange: str, lot_size: int | None) -> None:
        from ntrade.engines.morning_vah_val import MorningVAHVAL
        from ntrade.kernel.trading_session import TradingSession

        self._lot_size = lot_size or _default_lot_size(symbol)
        session = TradingSession.paper(initial_cash=self._initial_cash,
                                       session_id=f"paper-{symbol}")
        session.register(session.stock(symbol))
        # The tuned preset's sl_pad is absolute points calibrated for ~₹76k
        # index-futures notional; scale it to this symbol's price so a ₹100
        # stock doesn't get a 30% stop pad.
        kw = dict(MorningVAHVAL.TUNED)
        ref = MorningVAHVAL.TUNED_REF_PRICE
        try:
            state = self._pump._subs.get(symbol) or {}
            px = float(state.get("price") or 0.0)
            if px <= 0:
                bar = state.get("bar") or {}
                px = float(bar.get("close") or 0.0)
        except Exception:  # noqa: BLE001 — fall back to the reference price
            px = 0.0
        if px > 0:
            kw["sl_pad"] = kw["sl_pad"] * (px / ref)
        session.register_strategy(MorningVAHVAL(
            symbol=symbol, exchange=exchange, lot_size=self._lot_size, **kw,
        ), risk={
            # Sizing is the strategy's risk budget; these are sanity ceilings.
            "max_quantity": 10_000,
            "max_notional": 300_000_000.0,
            "max_daily_loss": 50_000.0,
            "max_drawdown_pct": 5.0,
        })
        # Make the pump form live 1m bars for this symbol (idempotent).
        try:
            self._pump.subscribe(symbol, exchange, "1m")
        except Exception:  # noqa: BLE001 — pump problems must not block paper
            log.warning("pump subscribe failed for %s", symbol)
        self._session = session
        self._symbol = symbol
        self._exchange = exchange
        self._fills = []
        self._signals = []

    def _feed_loop(self) -> None:
        with self._lock:
            session = self._session
        while not self._stop_event.is_set():
            bar = self._pump_bar()
            if bar is not None and bar.get("time") != self._last_bar_time:
                if self._pending_bar is not None:
                    self._feed_closed(self._pending_bar, session)
                self._pending_bar = dict(bar)
                self._last_bar_time = int(bar["time"])
            elif bar is not None:
                self._pending_bar = dict(bar)  # in-progress revision
            time.sleep(_POLL_S)
        # Flush the in-progress bar on stop so the final partial candle is
        # evaluated too (the captured session outlives stop() clearing it).
        with self._lock:
            bar = self._pending_bar
        if session is not None and bar is not None:
            self._feed_closed(bar, session)

    def _pump_bar(self) -> dict | None:
        with self._lock:
            symbol = self._symbol
            subs = self._pump._subs
        if not symbol:
            return None
        state = subs.get(symbol)
        return state.get("bar") if state else None

    def _feed_closed(self, bar: dict, session=None) -> None:
        with self._lock:
            session = session or self._session
            symbol = self._symbol
            exchange = self._exchange
        if session is None or symbol is None:
            return
        try:
            ts = datetime.fromtimestamp(int(bar["time"]), tz=IST)
            k = session.kernel
            k.bus.publish(QuoteEvent(
                symbol=symbol, exchange=exchange, ltp=float(bar["close"]),
                bid=0.0, ask=0.0, open=float(bar["open"]),
                high=float(bar["high"]), low=float(bar["low"]),
                volume=int(bar.get("volume", 0) or 0), ts=ts,
            ))
            k.bus.publish(CandleClosedEvent(
                symbol=symbol, exchange=exchange, timeframe="1m",
                open=float(bar["open"]), high=float(bar["high"]),
                low=float(bar["low"]), close=float(bar["close"]),
                volume=int(bar.get("volume", 0) or 0), ts=ts,
            ))
            # Keep the open position's LTP live so the status endpoint's
            # equity / unrealized PnL move with price (mirror of the backtest
            # simulator's _mark_to_market: Position.ltp otherwise only updates
            # on fills).
            position = k.ctx.portfolio.position(symbol)
            if position is not None:
                position.ltp = float(bar["close"])
            # Snapshot fills + signals for the status endpoint (bus history is
            # capped at 10k events, so keep our own growing lists).
            for event in k.bus.history:
                from ntrade.events.order import OrderFilledEvent
                from ntrade.events.risk import SignalGeneratedEvent
                if isinstance(event, OrderFilledEvent):
                    self._record_fill(event)
                elif isinstance(event, SignalGeneratedEvent):
                    self._record_signal(event)
        except Exception:  # noqa: BLE001 — a bad bar never kills the loop
            log.exception("paper feed failed on bar %s", bar.get("time"))

    def _record_fill(self, event: OrderFilledEvent) -> None:
        if any(f.get("order_id") == event.order_id for f in self._fills):
            return
        self._fills.append({
            "order_id": event.order_id,
            "symbol": event.symbol,
            "side": event.side,
            "quantity": int(event.quantity),
            "price": round(float(event.fill_price), 4),
            "ts": event.ts.isoformat(),
        })

    def _record_signal(self, event: SignalGeneratedEvent) -> None:
        meta = dict(event.metadata or {})
        if "exit_reason" in meta or meta.get("phase") == "signal":
            self._signals.append({
                "side": event.side,
                "quantity": int(event.quantity),
                "price": round(float(meta.get("reference_price") or 0.0), 4),
                "reason": meta.get("exit_reason"),
                "partial": bool(meta.get("partial")),
                "bias": meta.get("bias"),
                "sl": meta.get("sl"),
                "tp": meta.get("tp"),
                "ts": event.ts.isoformat(),
            })


def _service(request: Request) -> PaperTraderService:
    return request.app.state.paper


@paper_router.post("/start")
def paper_start(body: PaperStartRequest, request: Request) -> dict:
    return _service(request).start(body.symbol, body.exchange, body.lot_size)


@paper_router.post("/stop")
def paper_stop(request: Request) -> dict:
    return _service(request).stop()


@paper_router.get("/status")
def paper_status(request: Request) -> dict:
    return _service(request).status()
