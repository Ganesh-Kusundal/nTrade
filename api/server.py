"""FastAPI application factory for the nTrade market UI backend."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.live import LiveCandlePump, ws_router
from api.marketdata import FuturesMaster, build_service
from api.paper_trader import PaperTraderService, paper_router
from api.routes import router

log = logging.getLogger("api.server")


class _UiStaticFiles(StaticFiles):
    """Serve the built UI with SPA-correct caching.

    ``index.html`` must never be served from cache without revalidation — a
    rebuilt UI is otherwise invisible to already-open clients (observed in the
    desktop preview webview). Hashed assets are immutable and stay cacheable.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        if Path(full_path).name == "index.html":
            resp.headers["Cache-Control"] = "no-store"
        return resp


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.pump.start()
    yield
    await app.state.pump.stop()


def create_app(provider: str | None = None, env: dict | None = None,
               master: FuturesMaster | None = None, tick_s: float = 1.0,
               live_stream: bool = True) -> FastAPI:
    """Build the app around a :class:`MarketDataService`.

    ``provider``: ``dhan`` | ``parquet`` | ``synthetic`` (default). Env
    ``NTRADE_MARKET_PROVIDER`` is honoured when ``provider`` is None.
    ``master``/``tick_s`` are injectable for tests.

    ``live_stream`` (default ``True``): run the live candle pump. Streaming is
    still gated per exchange by :mod:`api.market_hours` (NSE 09:15–15:30,
    MCX 09:00–23:30 IST). Pass ``False`` for historical-only demos/tests.
    """
    service = build_service(provider, env=env, master=master)

    app = FastAPI(
        title="nTrade Market API",
        version="0.1.0",
        description="Futures instrument metadata, OHLCV candles, quotes and "
                    "live candle streaming for the nTrade trading UI.",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # local-only desktop/dev app
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.market = service
    app.state.pump = LiveCandlePump(service, tick_s=tick_s, enabled=live_stream)
    if live_stream and service.name == "dhan":
        app.state.pump.attach_broker_feed()

    # Paper trading: MorningVAHVAL on a ₹1M PaperBroker session fed by the
    # live candle pump (UI start/stop/status control). The seed cash mirrors
    # the real broker balance when streaming live Dhan (paper must be sized to
    # reality, not a fixed ₹1M).
    initial_cash = 1_000_000.0
    paper_store = None
    if os.environ.get("NTRADE_EVENT_STORE"):
        from ntrade.storage.event_store import EventStore
        paper_store = EventStore("data/events/paper")
    if live_stream and service.name == "dhan":
        try:
            broker_balance = float(service.provider.get_balance())
            if broker_balance > 0:
                initial_cash = broker_balance
        except Exception:  # noqa: BLE001 — a balance fetch must not block paper
            log.warning("broker balance unavailable; keeping paper default")
    app.state.paper = PaperTraderService(service, app.state.pump,
                                         initial_cash=initial_cash,
                                         store=paper_store)

    app.include_router(router)
    app.include_router(paper_router)
    app.include_router(ws_router)

    # Serve the built React app (ui/dist) when present — dev mode runs Vite
    # separately and proxies /api + /ws to this server.
    dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
    if dist.is_dir():
        app.mount("/", _UiStaticFiles(directory=str(dist), html=True), name="ui")

    return app
