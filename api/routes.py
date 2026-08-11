"""REST market routes — instrument metadata, candles, quotes.

All endpoints are read-only and broker-agnostic: they delegate to the
:class:`MarketDataService` attached to ``app.state.market``.
"""

from __future__ import annotations

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
    """Synthesized 1-second ticks for replay: each bar is extrapolated from its
    own OHLCV (anchored open/close, high/low touched, volume distributed).

    Compact per-bar wire format: ``bars`` = [{time, prices[], quantities[]}]
    where tick i of a bar is at ``time + i``. ``seconds`` = ticks per bar
    (60 for 1m, 300 for 5m, ...). Bars beyond the tick budget
    (``MAX_TICKS``) are dropped — the client falls back to plain bars for
    those. Deterministic: the same symbol/range always yields the same ticks
    (seeded per bar), so replay is reproducible.
    """
    service = _service(request)
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
        "seconds": _INTERVAL_MINUTES.get(interval, 1) * 60,
        "count": sum(len(b["prices"]) for b in ticks),
        "bars": ticks,
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
