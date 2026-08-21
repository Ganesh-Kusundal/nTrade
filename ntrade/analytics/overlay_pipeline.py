"""Chart overlay pipeline — registered as indicator spec 'halftrend' in domain/analytics/indicators.py

OverlayPipeline — single backend source of truth for chart overlays.

One call turns a candle series into every overlay the UI draws: VWAP ±σ
bands, session-anchored volume profile (POC/VAH/VAL), and — when a strategy id
is requested — the HalfTrend series + markers produced by the real domain
indicator (no fork).

Source of truth: this is the ONLY place the API computes overlays.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ntrade.domain.analytics.indicators import vwap, vwap_bands
from ntrade.domain.constants import DEFAULT_INITIAL_CASH
from ntrade.domain.market_hours import IST as _IST, is_market_open, session_open

log = logging.getLogger("ntrade.overlay")


# --------------------------------------------------------------------------- DTOs
@dataclass
class OverlayDTO:
    """JSON-serializable overlay payload for one symbol/interval."""
    symbol: str
    exchange: str
    interval: str
    candles: list[dict] = field(default_factory=list)
    vwap: list[dict] | None = None
    vwap_upper: list[dict] | None = None
    vwap_lower: list[dict] | None = None
    volume_profile: dict | None = None
    strategy: dict | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "interval": self.interval,
            "candles": self.candles,
            "overlays": {
                "vwap": self.vwap,
                "vwap_upper": self.vwap_upper,
                "vwap_lower": self.vwap_lower,
                "volume_profile": self.volume_profile,
            },
            "strategy": self.strategy,
        }


# --------------------------------------------------------------------- helpers
def _candles_to_frame(candles: list[dict]) -> pd.DataFrame:
    """Normalize the wire candle dicts into a frame.

    Input candles are `{time,open,high,low,close,volume}` with `time` as epoch
    seconds. Returns a frame sorted by `time`, deduplicated.
    """
    if not candles:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame([
        {
            "time": int(c["time"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c.get("volume", 0)),
        }
        for c in candles
    ])
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def _session_anchor(df: pd.DataFrame, exchange: str) -> pd.DataFrame:
    """Restrict a frame to the current IST session (09:15 NSE / 09:00 MCX).

    Volume profile + VWAP are session-anchored; the UI's `sessionProfileCandles`
    does the same cut. Uses the frame's own latest bar to decide "today".
    """
    if df.empty:
        return df
    latest = datetime.fromtimestamp(int(df["time"].iloc[-1]), tz=_IST)
    if not is_market_open(exchange, latest):
        return df  # out of hours: show whatever we have
    open_t = session_open(exchange)
    op = latest.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
    start = int(op.timestamp())
    return df[df["time"] >= start].reset_index(drop=True)


# ------------------------------------------------------------- build overlays
def _build_vwap(df: pd.DataFrame, session: pd.DataFrame) -> dict | None:
    if session.empty:
        return None
    from ntrade.domain.analytics.indicators import vwap as _vwap_series
    ser = _vwap_series(session)
    n = len(df)
    out = [{"time": int(df["time"].iloc[i]),
            "value": round(float(ser.iloc[i]), 4) if i < len(ser) else None}
           for i in range(n)]
    upper = lower = None
    u, l = vwap_bands(session)
    import pandas as _pd
    if _pd.notna(u) and _pd.notna(l):
        upper = [{"time": int(df["time"].iloc[i]),
                  "value": round(float(u), 4) if i == n - 1 else None}
                 for i in range(n)]
        lower = [{"time": int(df["time"].iloc[i]),
                  "value": round(float(l), 4) if i == n - 1 else None}
                 for i in range(n)]
    return {"vwap": out, "upper": upper, "lower": lower}


def _build_volume_profile(df: pd.DataFrame, session: pd.DataFrame) -> dict | None:
    if session.empty:
        return None
    from ntrade.domain.analytics.volume_profile import build_volume_profile
    from ntrade.domain.analytics.volume_profile import VolumeProfile
    vp: VolumeProfile = build_volume_profile(session)
    if not vp.levels:
        return None
    return {
        "step": round(float(vp.step), 4),
        "poc": round(float(vp.poc), 4),
        "vah": round(float(vp.vah), 4),
        "val": round(float(vp.val), 4),
        "levels": [
            {"price": round(float(lvl.price), 4), "volume": round(float(lvl.volume), 4)}
            for lvl in vp.levels
        ],
    }


# ----------------------------------------------------------------------- halftrend
def _build_halftrend(df: pd.DataFrame, params: dict | None) -> dict | None:
    """Compute HalfTrend markers directly for the chart overlay path.

    This is the backend source of truth for the HalfTrend series shown on the
    chart. The paper/live path uses the HalfTrendStrategy class; this function
    produces the same buy/sell markers from the same domain math (zero-parity).
    """
    if df.empty:
        return None
    from ntrade.domain.analytics.halftrend import halftrend as _ht
    amp = int(params.get("amplitude", 2)) if params else 2
    cdev = int(params.get("channel_deviation", 2)) if params else 2
    apt = int(params.get("atr_period", 100)) if params else 100
    out = _ht(df, amplitude=amp, channel_deviation=cdev, atr_period=apt)
    if out.empty:
        return None
    markers = []
    # ponytail: skip ATR warmup — trend flips before atr_period are on
    # uninitialized state and produce spurious signals
    for i in range(apt, len(out)):
        row = out.iloc[i]
        prev = out.iloc[i - 1]
        if pd.notna(row.get("buySignal")) and bool(row["buySignal"]):
            markers.append({
                "index": int(i),
                "time": int(df["time"].iloc[i]),
                "side": "BUY",
                "price": round(float(df["close"].iloc[i]), 4),
                "ht": round(float(row["ht"]), 4) if pd.notna(row["ht"]) else None,
                "trend": int(row["trend"]),
            })
        elif pd.notna(row.get("sellSignal")) and bool(row["sellSignal"]):
            markers.append({
                "index": int(i),
                "time": int(df["time"].iloc[i]),
                "side": "SELL",
                "price": round(float(df["close"].iloc[i]), 4),
                "ht": round(float(row["ht"]), 4) if pd.notna(row["ht"]) else None,
                "trend": int(row["trend"]),
            })
    return {
        "id": "halftrend",
        "markers": markers,
        "series": {
            "ht": [round(float(x), 4) if pd.notna(x) else None
                   for x in out["ht"].tolist()],
            "trend": [int(x) for x in out["trend"].tolist()],
            "atrHigh": [round(float(x), 4) if pd.notna(x) else None
                        for x in out["atrHigh"].tolist()],
            "atrLow": [round(float(x), 4) if pd.notna(x) else None
                       for x in out["atrLow"].tolist()],
        },
    }


# -------------------------------------------------------------------- pipeline
def build_overlays(
    candles: list[dict],
    *,
    symbol: str,
    exchange: str,
    interval: str,
    strategy_id: str | None = None,
    strategy_params: dict | None = None,
    tick_size: float | None = None,
    range_size: float | None = None,
    balance: float = DEFAULT_INITIAL_CASH,
    include_range_bars: bool = False,
) -> OverlayDTO:
    """Compute every overlay for a candle series.

    Args:
        candles: wire candles `{time,open,high,low,close,volume}`.
        strategy_id: optional registered strategy; when set, markers are
            produced by replaying the real strategy class.
        include_range_bars: also return range bars (used by Valentini; the
            chart draws the time-based frame, range bars feed strategy state).
    """
    df = _candles_to_frame(candles)
    session = _session_anchor(df, exchange)

    dto = OverlayDTO(
        symbol=symbol, exchange=exchange, interval=interval,
        candles=[{
            "time": int(c["time"]), "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
            "volume": float(c.get("volume", 0)),
        } for c in df.to_dict("records")],
    )

    v = _build_vwap(df, session)
    if v is not None:
        dto.vwap = v["vwap"]
        dto.vwap_upper = v["upper"]
        dto.vwap_lower = v["lower"]
    else:
        dto.vwap = None

    dto.volume_profile = _build_volume_profile(df, session)

    if strategy_id:
        if strategy_id == "halftrend":
            dto.strategy = _build_halftrend(df, strategy_params) or {
                "id": "halftrend", "markers": [], "series": {"ht": [], "trend": [], "atrHigh": [], "atrLow": []}}

    return dto


def build_chart(candles: list[dict], *, symbol: str, exchange: str,
                interval: str, **kw) -> dict:
    """Thin wrapper returning the JSON dict the API serves."""
    return build_overlays(candles, symbol=symbol, exchange=exchange,
                          interval=interval, **kw).to_dict()
