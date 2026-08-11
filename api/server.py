"""FastAPI application factory for the nTrade market UI backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.live import LiveCandlePump, ws_router
from api.marketdata import FuturesMaster, build_service
from api.routes import router


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

    app.include_router(router)
    app.include_router(ws_router)

    # Serve the built React app (ui/dist) when present — dev mode runs Vite
    # separately and proxies /api + /ws to this server.
    dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
    if dist.is_dir():
        app.mount("/", _UiStaticFiles(directory=str(dist), html=True), name="ui")

    return app
