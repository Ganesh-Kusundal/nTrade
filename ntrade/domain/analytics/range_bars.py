"""Range bar generator — price-based bars from 1m OHLCV (Valentini scalper).

A range bar closes when price travels a fixed ``range_size`` (``high - low
>= range_size``), regardless of how many 1m candles that took. This is the
bar primitive Valentini's scalper works on: time-based noise is filtered out
and only price action matters.

Dhan serves only time-based history, so we simulate a canonical path through
each 1m candle (open -> high -> low -> close, or the mirrored order for a
bearish candle) and close a bar the moment its span fills. Candle volume is
distributed proportionally to the path segments each bar consumed — a
documented approximation (no trade tape on Dhan).

Auto range: ``ATR(14)`` scaled to a clean tick grid when ``tick_size`` is
given.
"""

from __future__ import annotations

import pandas as pd

from ntrade.domain.analytics.indicators import atr

_BAR_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "is_complete"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_BAR_COLUMNS)


def calc_auto_range(df: pd.DataFrame, atr_period: int = 14, multiplier: float = 1.0,
                    tick_size: float | None = None) -> float:
    """ATR-derived range size, optionally rounded to a tick grid.

    Falls back to a sane 10.0 default when ATR cannot be computed (empty
    frame / all-NaN). Never returns <= 0.
    """
    raw = 10.0
    if df is not None and not df.empty:
        a = atr(df, atr_period)
        if len(a) and pd.notna(a.iloc[-1]) and a.iloc[-1] > 0:
            raw = float(a.iloc[-1]) * multiplier
    if tick_size and tick_size > 0:
        raw = max(tick_size, round(raw / tick_size) * tick_size)
    return max(float(raw), 0.5)


def _candle_path(o: float, h: float, lo: float, c: float) -> list[float]:
    """Canonical path through one candle: O -> extreme -> other -> C.

    Bullish: O -> H -> L -> C  (sweep up, wick down, close mid)
    Bearish: O -> L -> H -> C  (sweep down, wick up, close mid)
    """
    return [o, h, lo, c] if c >= o else [o, lo, h, c]


def build_range_bars(df: pd.DataFrame, range_size: float | None = None,
                     atr_period: int = 14, multiplier: float = 1.0,
                     tick_size: float | None = None) -> pd.DataFrame:
    """Convert a 1m OHLCV frame into range bars.

    Returns a frame with columns ``timestamp/open/high/low/close/volume/
    is_complete``. Bars are labelled with the timestamp of the last source
    candle they consumed (right edge). The final open bar is marked
    ``is_complete=False``; earlier bars are complete.
    """
    if df is None or df.empty or "open" not in df:
        return _empty()
    size = range_size if range_size and range_size > 0 else calc_auto_range(
        df, atr_period=atr_period, multiplier=multiplier, tick_size=tick_size)

    bars: list[dict] = []
    cur: dict | None = None  # open/high/low/volume/ts of the in-progress bar

    for _, row in df.iterrows():
        o, h, lo, c = (float(row["open"]), float(row["high"]),
                       float(row["low"]), float(row["close"]))
        raw_vol = row.get("volume", 0)
        vol = float(raw_vol) if not pd.isna(raw_vol) else 0.0
        path = _candle_path(o, h, lo, c)
        segments = len(path) - 1
        vol_per_seg = vol / segments if segments else 0.0
        ts = row["timestamp"]
        for i in range(1, len(path)):
            px = path[i]
            if cur is None:
                # New bar starts at the segment's origin price.
                cur = {"open": path[i - 1], "high": max(path[i - 1], px),
                       "low": min(path[i - 1], px), "close": px,
                       "volume": vol_per_seg, "ts": ts}
            else:
                cur["high"] = max(cur["high"], px)
                cur["low"] = min(cur["low"], px)
                cur["close"] = px
                cur["volume"] += vol_per_seg
                cur["ts"] = ts
            if cur["high"] - cur["low"] >= size:
                bars.append(cur)
                cur = None

    trailing_partial = cur is not None  # last source candle left a bar open
    if trailing_partial:  # trailing incomplete bar (close already set)
        bars.append(cur)

    if not bars:
        return _empty()
    out = pd.DataFrame(bars, columns=["open", "high", "low", "close", "volume", "ts"])
    # Only the trailing partial bar is incomplete — if the final source candle
    # completed a bar exactly (trailing_partial False), every bar is complete.
    is_complete = [True] * len(bars)
    if trailing_partial:
        is_complete[-1] = False
    out["is_complete"] = is_complete
    out["timestamp"] = out["ts"]
    out["volume"] = out["volume"].round(4)
    return out[_BAR_COLUMNS].reset_index(drop=True)


def swing_bias(bars: pd.DataFrame) -> str | None:
    """Directional vote from the last two COMPLETED range bars.

    Bullish when the latest completed bar makes a higher high AND a higher low
    than the prior; bearish on a lower high + lower low; ``None`` otherwise
    (fewer than two completed bars, or an inside/outside bar — no vote).
    """
    if bars is None or bars.empty or "is_complete" not in bars:
        return None
    done = bars[bars["is_complete"].astype(bool)]
    if len(done) < 2:
        return None
    p, c = done.iloc[-2], done.iloc[-1]
    ph, pl = float(p["high"]), float(p["low"])
    ch, cl = float(c["high"]), float(c["low"])
    if ch > ph and cl > pl:
        return "BUY"
    if ch < ph and cl < pl:
        return "SELL"
    return None
