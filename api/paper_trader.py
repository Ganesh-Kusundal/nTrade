"""Paper trading service + REST routes.

Runs ``MorningVAHVAL`` on a ₹1M ``PaperBroker`` session fed by the live
candle pump — a UI control ("paper trade on the selected symbol") that
exercises the real kernel pipeline (real ``TickEvent`` → MarketEngine →
CandleEngine → strategy → risk → paper fills) with zero real orders.

The pump's ``on_tick`` listener publishes every real tick into the paper
kernel; a snapshot loop then polls the kernel (LTP, position sync, fill /
signal recording) so the status endpoint tracks the canonical engine's
decisions. No ``QuoteEvent``/``CandleClosedEvent`` is ever hand-injected.
Starting paper requires a real live feed; outside exchange hours the pump
produces no ticks and the paper session simply idles.

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
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ntrade.domain.market_hours import IST
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.events.risk import SignalGeneratedEvent
from ntrade.storage.event_store import EventStore

log = logging.getLogger("api.paper")

paper_router = APIRouter(prefix="/api/paper", tags=["paper"])

_POLL_S = 1.0


class PaperStartRequest(BaseModel):
    symbol: str
    exchange: str = "NFO"
    lot_size: int | None = None
    strategy: str | None = None  # registered strategy id (default morning_vah_val)
    strategy_params: dict | None = None


class PaperTraderService:
    """Owns at most one paper session at a time, keyed to a symbol."""

    def __init__(self, market, pump, initial_cash: float = 1_000_000.0,
             store: EventStore | None = None):
        self._market = market
        self._pump = pump
        self._initial_cash = float(initial_cash)
        self._store = store
        self._lock = threading.Lock()
        self._session = None
        self._symbol: str | None = None
        self._exchange: str = "NFO"
        self._lot_size = 1
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._unsub: Callable[[], None] | None = None
        self._started_at: str | None = None
        self._error: str | None = None
        self._fills: list[dict] = []
        self._signals: list[dict] = []
        self._strategy_id: str | None = None
        self._strategy_params: dict | None = None
        self._strategy_inst = None  # live Strategy instance (for snapshots)

    # ------------------------------------------------------------------ API
    def start(self, symbol: str, exchange: str = "NFO",
              lot_size: int | None = None, strategy: str | None = None,
              strategy_params: dict | None = None) -> dict:
        symbol = symbol.strip().upper()
        with self._lock:
            if not self._pump._real_feed or not self._pump.enabled:
                raise HTTPException(
                    status_code=422,
                    detail="paper trading requires a real live feed (run --provider dhan --live-stream)",
                )
            if self._session is not None:
                already = True
            else:
                already = False
                try:
                    self._build_session(symbol, exchange.upper(), lot_size,
                                        strategy, strategy_params)
                except HTTPException:
                    raise  # 422 from _resolve_lot_size passes through untouched
                except Exception as exc:  # noqa: BLE001 — surface to the caller
                    log.exception("paper start failed for %s", symbol)
                    self._error = str(exc)
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                self._started_at = datetime.now(tz=IST).isoformat()
                self._error = None
                self._stop_event.clear()
                self._unsub = self._pump.on_tick(self._on_pump_tick)
                self._thread = threading.Thread(target=self._snapshot_loop, daemon=True,
                                                name="paper-snapshot")
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
            self._strategy_id = None
            self._strategy_params = None
            self._strategy_inst = None
            self._stop_event.set()
            unsub = self._unsub
            self._unsub = None
            if unsub is not None:
                try:
                    unsub()
                except Exception:  # noqa: BLE001
                    pass
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
            "strategy": None,
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
        # Strategy snapshot from the SAME instance running in the kernel —
        # zero-parity with chart overlay + live. No recomputation on the API.
        strategy_snapshot = None
        inst = self._strategy_inst
        if inst is not None:
            snap: dict = {"id": self._strategy_id}
            for attr in ("phase", "bias", "_day_pnl"):
                try:
                    val = getattr(inst, attr)
                    snap[attr.lstrip("_")] = val
                except Exception:  # noqa: BLE001
                    pass
            profile = getattr(inst, "_profile", None)
            if profile is not None and profile.levels:
                snap["levels"] = [{
                    "vah": round(float(profile.vah), 4),
                    "val": round(float(profile.val), 4),
                    "poc": round(float(profile.poc), 4),
                }]
            active = getattr(inst, "_active", None)
            if active is not None:
                snap["open_trade"] = {
                    "side": active.get("side"),
                    "entry": round(float(active.get("entry", 0.0)), 4),
                    "sl": round(float(active.get("sl", 0.0) or 0.0), 4),
                    "tp": (round(float(active["tp"]), 4)
                           if active.get("tp") is not None else None),
                    "qty": int(active.get("qty", 0)),
                }
            strategy_snapshot = snap
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
            "strategy": strategy_snapshot,
        }

    # ------------------------------------------------------------- internals
    def _resolve_lot_size(self, symbol: str, lot_size: int | None) -> int:
        """Contract lot size from the instrument master; explicit lot_size wins.

        Unknown symbols fail loudly (422) instead of minting a hardcoded
        default — the paper session must never guess a size the broker's
        contract actually contradicts.
        """
        if lot_size and lot_size > 0:
            return int(lot_size)
        contract = self._market.master.resolve(symbol)
        if contract is not None and contract.lot_size:
            return int(contract.lot_size)
        raise HTTPException(
            status_code=422,
            detail=f"unknown lot size for {symbol}; pass lot_size explicitly",
        )

    def _build_session(self, symbol: str, exchange: str, lot_size: int | None,
                       strategy: str | None, strategy_params: dict | None) -> None:
        from ntrade.registry import strategy as _strategy_reg
        from ntrade.engines.strategies import _strategy_classes
        from ntrade.kernel.trading_session import TradingSession

        # Resolve the strategy by id through the registry — no hardcoded class
        # import. The spec carries the constructor defaults; _strategy_classes
        # maps the spec id to the concrete implementation. Default = tuned
        # morning_vah_val (kept for backward-compat callers that omit strategy).
        strategy_id = (strategy or "morning_vah_val").strip()
        spec = _strategy_reg.get(strategy_id)
        cls = _strategy_classes[spec.id]

        self._lot_size = self._resolve_lot_size(symbol, lot_size)
        session = TradingSession.paper(initial_cash=self._initial_cash,
                                       session_id=f"paper-{symbol}",
                                       store=self._store)
        session.register(session.stock(symbol))
        # The tuned preset's sl_pad is absolute points calibrated for ~₹76k
        # index-futures notional; scale it to this symbol's price so a ₹100
        # stock doesn't get a 30% stop pad.
        kw = dict(getattr(cls, "TUNED", {}))
        ref = getattr(cls, "TUNED_REF_PRICE", 0.0) or 0.0
        try:
            state = self._pump._subs.get(symbol) or {}
            bar = state.get("bar") or {}
            px = float(bar.get("close") or 0.0)
        except Exception:  # noqa: BLE001 — fall back to the reference price
            px = 0.0
        if px > 0 and ref > 0:
            kw["sl_pad"] = kw.get("sl_pad", 0.0) * (px / ref)
        if strategy_params:
            kw.update(strategy_params)
        inst = cls(symbol=symbol, exchange=exchange, lot_size=self._lot_size, **kw)
        session.register_strategy(inst, risk={
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
        self._strategy_id = strategy_id
        self._strategy_params = strategy_params
        self._strategy_inst = inst
        self._fills = []
        self._signals = []

    def _on_pump_tick(self, symbol: str, exchange: str, price: float,
                      qty: int, ts: datetime) -> None:
        session = self._session
        if session is None or symbol != self._symbol:
            return
        try:
            session.kernel.bus.publish(TickEvent(
                symbol=symbol, exchange=exchange, price=float(price),
                quantity=int(qty), ts=ts,
            ))
        except Exception:  # noqa: BLE001
            log.exception("paper tick publish failed")

    def _snapshot_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                session = self._session
                if session is not None:
                    k = session.kernel
                    # The bus RLock is the kernel's serialization point: live
                    # mode has multiple producer threads and the shared
                    # read-models must never observe a torn dispatch (EventBus
                    # docstring). This snapshot loop mutates the same
                    # read-models (position LTP, portfolio sync) and scans
                    # history, so it must run inside that lock or it races the
                    # pump's tick dispatch — concurrent list/read-model
                    # mutation that hangs or segfaults under load.
                    with k.bus._lock:
                        bar = (self._pump._subs.get(self._symbol) or {}).get("bar")
                        if bar:
                            position = k.ctx.portfolio.position(self._symbol)
                            if position is not None:
                                position.ltp = float(bar["close"])
                        try:
                            k.sync_positions()
                        except Exception:  # noqa: BLE001
                            pass  # reconciliation failure keeps prior state
                        for event in k.bus.history:
                            if isinstance(event, OrderFilledEvent):
                                self._record_fill(event)
                            elif isinstance(event, SignalGeneratedEvent) and (
                                "exit_reason" in (event.metadata or {})
                                or (event.metadata or {}).get("phase") == "signal"
                            ):
                                self._record_signal(event)
            except Exception:  # noqa: BLE001
                log.exception("paper snapshot failed")
            time.sleep(_POLL_S)

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
    return _service(request).start(
        body.symbol, body.exchange, body.lot_size,
        strategy=body.strategy, strategy_params=body.strategy_params)


@paper_router.post("/stop")
def paper_stop(request: Request) -> dict:
    return _service(request).stop()


@paper_router.get("/status")
def paper_status(request: Request) -> dict:
    return _service(request).status()
