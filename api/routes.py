"""REST market routes — instrument metadata, candles, quotes.

All endpoints are read-only and broker-agnostic: they delegate to the
:class:`MarketDataService` attached to ``app.state.market``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from ntrade.domain.constants import Exchange

from api.marketdata import (
    MAX_CANDLES_LIMIT,
    MarketDataError,
    _INTERVAL_MINUTES,
    is_valid_symbol,
    parse_iso,
)

router = APIRouter(prefix="/api/market", tags=["market"])

# UI intervals the backend accepts (Dhan natively serves 1m/5m/15m/60m/DAY;
# 1h and 1D alias to those).
Interval = Literal["1m", "5m", "15m", "1h", "1D"]


def _service(request: Request):
    return request.app.state.market


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _validate_symbol_params(
    symbol: str, start: str | None, end: str | None
) -> tuple[str, datetime | None, datetime | None]:
    """Shared validation for symbol + ISO-8601 window (candles/ticks/chart).

    Returns the normalized (SYMBOL, start_dt, end_dt) tuple or raises the
    same 422 HTTPException the inline blocks used to raise.
    """
    symbol = symbol.strip().upper()
    if not is_valid_symbol(symbol):
        raise _bad(f"invalid symbol {symbol!r}")
    try:
        start_dt = parse_iso(start, "start")
        end_dt = parse_iso(end, "end")
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        raise _bad("start must be <= end")
    return symbol, start_dt, end_dt


@router.get("/provider")
def get_provider(request: Request) -> dict:
    """Active data provider + instrument-master status (drives UI badges)."""
    return _service(request).describe()


@router.get("/roots")
def list_roots(request: Request) -> dict:
    """All root symbols available as futures (NIFTY, BANKNIFTY, ...)."""
    service = _service(request)
    if not service.master.loaded:
        raise HTTPException(
            status_code=503,
            detail="instrument master not found on disk (Dependencies/*all_instrument*.csv)",
        )
    return {"roots": service.roots(), "source": service.name}


@router.get("/roots/{root}/contracts")
def list_contracts(root: str, request: Request) -> dict:
    """Futures contracts + expiries for a root, nearest expiry first."""
    service = _service(request)
    root = root.strip().upper()
    if not service.has_root(root):
        raise HTTPException(status_code=404, detail=f"no futures contracts for root {root!r}")
    contracts = service.contracts(root)
    exchange = contracts[0]["exchange"] if contracts else Exchange.DERIVATIVES
    return {
        "root": root,
        "exchange": exchange,
        "contracts": contracts,
        "source": service.name,
    }


@router.get("/candles")
def get_candles(
    request: Request,
    symbol: str = Query(..., description="Custom/trading symbol, e.g. 'NIFTY OCT FUT'"),
    exchange: str = Query(Exchange.DERIVATIVES),
    interval: Interval = Query("1m"),
    start: str | None = Query(None, description="ISO-8601 start (naive = IST)"),
    end: str | None = Query(None, description="ISO-8601 end (naive = IST)"),
    limit: int = Query(MAX_CANDLES_LIMIT, ge=1, le=MAX_CANDLES_LIMIT),
) -> dict:
    """Historical OHLCV candles normalized to ``{time,open,high,low,close,volume}``.

    ``time`` is UTC epoch seconds. Candles are sorted ascending and de-duped
    by timestamp. An empty list means the provider has no data for the range
    (the UI renders its no-data state, not an error).
    """
    service = _service(request)
    symbol, start_dt, end_dt = _validate_symbol_params(symbol, start, end)
    try:
        candles = service.candles(symbol=symbol, exchange=exchange.strip().upper(),
                                  interval=interval, start=start_dt, end=end_dt,
                                  limit=limit)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "symbol": symbol,
        "exchange": exchange.strip().upper(),
        "interval": interval,
        "source": service.name,
        "count": len(candles),
        "candles": candles,
    }


@router.get("/ticks")
def get_ticks(
    request: Request,
    symbol: str = Query(..., description="Custom/trading symbol, e.g. 'NIFTY OCT FUT'"),
    exchange: str = Query(Exchange.DERIVATIVES),
    interval: Interval = Query("1m"),
    start: str | None = Query(None, description="ISO-8601 start (naive = IST)"),
    end: str | None = Query(None, description="ISO-8601 end (naive = IST)"),
    limit: int = Query(MAX_CANDLES_LIMIT, ge=1, le=MAX_CANDLES_LIMIT),
) -> dict:
    """Replay ticks: real recorded ticks for live/parquet providers, or
    deterministic synthesized ticks for the ``synthetic`` provider (each bar
    extrapolated from its own OHLCV: anchored open/close, high/low touched,
    volume distributed).

    Compact per-bar wire format: ``bars`` = [{time, prices[], quantities[]}]
    where tick i of a bar is at ``time + i``. ``seconds`` = ticks per bar
    (60 for 1m, 300 for 5m, ...). ``synthetic`` is true only when the ticks
    were fabricated by the provider; when no recorded ticks exist for the
    range a ``reason`` is included so the client can fall back to plain bars.
    Deterministic: the same symbol/range always yields the same ticks
    (seeded per bar), so replay is reproducible.
    """
    service = _service(request)
    symbol, start_dt, end_dt = _validate_symbol_params(symbol, start, end)
    try:
        ticks = service.ticks(symbol=symbol, exchange=exchange.strip().upper(),
                              interval=interval, start=start_dt, end=end_dt,
                              limit=limit)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "symbol": symbol,
        "exchange": exchange.strip().upper(),
        "interval": interval,
        "source": service.name,
        "synthetic": service.name == "synthetic",
        "seconds": _INTERVAL_MINUTES.get(interval, 1) * 60,
        "count": sum(len(b["prices"]) for b in ticks),
        "bars": ticks,
        **({} if ticks else {"reason": "no recorded ticks for range — replay falls back to plain bars"}),
    }


@router.get("/quote")
def get_quote(
    request: Request,
    symbol: str = Query(...),
    exchange: str = Query(Exchange.DERIVATIVES),
) -> dict:
    """Current quote for a symbol (ltp, change %, day OHLC, volume, source)."""
    service = _service(request)
    symbol = symbol.strip().upper()
    if not is_valid_symbol(symbol):
        raise _bad(f"invalid symbol {symbol!r}")
    try:
        return service.quote(symbol=symbol, exchange=exchange.strip().upper())
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/health")
def health(request: Request) -> dict:
    service = _service(request)
    return {"ok": True, "provider": service.name}


@router.get("/chart")
def get_chart(
    request: Request,
    symbol: str = Query(..., description="Custom/trading symbol, e.g. 'NIFTY OCT FUT'"),
    exchange: str = Query(Exchange.DERIVATIVES),
    interval: str = Query("1m", description="1m | 5m | 15m | 1h | 1D | Range"),
    start: str | None = Query(None, description="ISO-8601 start (naive = IST)"),
    end: str | None = Query(None, description="ISO-8601 end (naive = IST)"),
    strategy: str | None = Query(None, description="Registered strategy id (optional)"),
    limit: int = Query(MAX_CANDLES_LIMIT, ge=1, le=MAX_CANDLES_LIMIT),
    tick_size: float | None = Query(None),
    range_size: float | None = Query(None),
) -> dict:
    """Single backend-calc chart payload: candles + every overlay + optional
    strategy markers. The UI renders this verbatim; it never reimplements the
    indicator/strategy math (zero-parity with paper/live).

    ``interval='Range'`` builds price-based range bars server-side.
    """
    service = _service(request)
    symbol, start_dt, end_dt = _validate_symbol_params(symbol, start, end)
    try:
        return service.build_chart(
            symbol=symbol, exchange=exchange.strip().upper(), interval=interval,
            start=start_dt, end=end_dt, limit=limit, strategy=strategy,
            tick_size=tick_size, range_size=range_size,
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/catalog")
def get_catalog(request: Request) -> dict:
    """Indicator + strategy registry metadata for the FE renderer.

    Replaces the hard-coded TS registry ``run`` wiring: the FE fetches this
    once at init and keeps only plot metadata + ids (no client math).
    """
    from ntrade.registry import indicator as indicator_reg
    from ntrade.registry import strategy as strategy_reg

    indicators = []
    for spec in indicator_reg.registry.values():
        indicators.append({
            "id": spec.id,
            "label": spec.label,
            "params": spec.params,
            "series": spec.series,
            "plot": {
                "series_key": spec.plot.series_key if spec.plot else None,
                "pane": spec.plot.pane if spec.plot else "overlay",
                "color": spec.plot.color if spec.plot else None,
            } if spec.plot else None,
        })
    strategies = []
    for spec in strategy_reg.registry.values():
        strategies.append({
            "id": spec.id,
            "label": spec.label,
            "params": spec.params,
            "indicators": spec.indicators,
        })
    return {"indicators": indicators, "strategies": strategies}
