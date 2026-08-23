"""Indicator bundle — pure functions over OHLCV dataframes.

No external TA library required; keeps the domain layer dependency-free.
"""

from __future__ import annotations

import logging
import typing

import pandas as pd

from ntrade.registry import IndicatorSpec, PlotSpec, indicator

if typing.TYPE_CHECKING:  # explicit coupling — 'halftrend' spec calc lives in ntrade.analytics.overlay_pipeline
    import ntrade.analytics.overlay_pipeline  # noqa: F401

logger = logging.getLogger("ntrade.indicators")


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    close = df["close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    # Zero-loss streaks (strong uptrends) must map to RSI 100, not NaN.
    safe_loss = avg_loss.where(avg_loss > 0, 1e-10)
    rs = avg_gain / safe_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.clip(lower=0.0, upper=100.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index (ADX) with +DI and -DI (Wilder's DMI).
    
    Returns a DataFrame with columns ['adx', 'plus_di', 'minus_di'].
    """
    if df is None or df.empty or len(df) < 2:
        return pd.DataFrame(columns=["adx", "plus_di", "minus_di"], index=df.index if df is not None else None)
    
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    
    smooth_tr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    smooth_plus_dm = plus_dm.ewm(alpha=1 / period, min_periods=period).mean()
    smooth_minus_dm = minus_dm.ewm(alpha=1 / period, min_periods=period).mean()
    
    safe_tr = smooth_tr.where(smooth_tr > 0, 1e-10)
    plus_di = (100.0 * smooth_plus_dm / safe_tr).clip(lower=0.0, upper=100.0)
    minus_di = (100.0 * smooth_minus_dm / safe_tr).clip(lower=0.0, upper=100.0)
    
    di_sum = plus_di + minus_di
    safe_di_sum = di_sum.where(di_sum > 0, 1e-10)
    dx = (100.0 * (plus_di - minus_di).abs() / safe_di_sum).clip(lower=0.0, upper=100.0)
    
    adx_series = dx.ewm(alpha=1 / period, min_periods=period).mean().clip(lower=0.0, upper=100.0)
    
    return pd.DataFrame(
        {"adx": adx_series, "plus_di": plus_di, "minus_di": minus_di},
        index=df.index,
    )


def sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Simple moving average over close."""
    return df["close"].astype(float).rolling(period, min_periods=period).mean()


def ema(df: pd.DataFrame, period: int = 9) -> pd.Series:
    """Exponential moving average over close (standard ewm, span = period)."""
    return df["close"].astype(float).ewm(span=period, min_periods=period).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    vol = df.get("volume", pd.Series(1.0, index=df.index)).astype(float)
    cum_vol = vol.cumsum().replace(0, pd.NA)
    return (typical * vol).cumsum() / cum_vol


def vwap_bands(df: pd.DataFrame, num_std: float = 2.0) -> tuple[float, float]:
    """VWAP standard-deviation bands ``(upper, lower)`` at the last bar.

    Institutional-style: band = VWAP ± num_std · σ, where σ is the
    volume-weighted standard deviation of the typical price around the
    cumulative VWAP. Used by the Valentini model as the overbought/
    oversold context (entry at VAL/VAH vs VWAP bias). Returns ``(nan, nan)``
    for empty input.
    """
    if df is None or df.empty or "close" not in df:
        return float("nan"), float("nan")
    typical = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    vol = df.get("volume", pd.Series(1.0, index=df.index)).astype(float)
    cum_vol = vol.cumsum().replace(0, pd.NA)
    v = vwap(df)
    # volume-weighted variance of typical around the running VWAP
    var = ((typical - v) ** 2 * vol).cumsum() / cum_vol
    sigma = var ** 0.5
    last = v.iloc[-1]
    band = num_std * sigma.iloc[-1]
    return float(last + band), float(last - band)


def wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted moving average. Most recent bar carries the most weight.

    Vectorized via numpy convolution (the rolling .apply form allocates a
    Python lambda per window and is unusably slow on long backtests).
    """
    import numpy as np
    period = int(period)
    weights = np.arange(period, 0, -1, dtype=float)  # period..1
    weights /= weights.sum()
    vals = series.to_numpy(dtype=float)
    out = np.full_like(vals, np.nan)
    # 'valid' slides the kernel fully inside; pad front so index aligns.
    if len(vals) >= period:
        conv = np.convolve(vals, weights, mode="valid")
        out[period - 1:] = conv
    return pd.Series(out, index=series.index)


def hma(df: pd.DataFrame, period: int = 21) -> pd.Series:
    """Hull Moving Average — low-lag trend baseline.

    ``HMA = WMA(2*WMA(price, n/2) - WMA(price, n), sqrt(n))``. Uses typical
    price (the same source as VWAP) so the baseline and the value band line up.
    """
    typical = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    half = wma(typical, max(2, int(period / 2)))
    full = wma(typical, int(period))
    raw = 2 * half - full
    return wma(raw, max(2, int(round(period ** 0.5))))


def supertrend(df: pd.DataFrame, atr_period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Adds STX_<atr_period>_<multiplier> column: 'up' or 'down' per candle."""
    out = df.copy()
    hl2 = (out["high"].astype(float) + out["low"].astype(float)) / 2.0
    atr_series = atr(out, atr_period)
    upper = hl2 + multiplier * atr_series
    lower = hl2 - multiplier * atr_series
    close = out["close"].astype(float)

    final_upper = upper.copy()
    final_lower = lower.copy()
    st = pd.Series("up", index=out.index)
    for i in range(1, len(out)):
        prev_close = close.iloc[i - 1]
        if pd.notna(final_upper.iloc[i - 1]):
            if close.iloc[i] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = upper.iloc[i]
            else:
                final_upper.iloc[i] = min(upper.iloc[i], final_upper.iloc[i - 1])
        if pd.notna(final_lower.iloc[i - 1]):
            if close.iloc[i] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = lower.iloc[i]
            else:
                final_lower.iloc[i] = max(lower.iloc[i], final_lower.iloc[i - 1])
        if pd.isna(final_upper.iloc[i - 1]) or pd.isna(final_lower.iloc[i - 1]):
            final_upper.iloc[i] = upper.iloc[i]
            final_lower.iloc[i] = lower.iloc[i]
            continue
        if prev_close <= final_upper.iloc[i - 1] and close.iloc[i] > final_upper.iloc[i - 1]:
            st.iloc[i] = "up"
        elif prev_close >= final_lower.iloc[i - 1] and close.iloc[i] < final_lower.iloc[i - 1]:
            st.iloc[i] = "down"
        else:
            st.iloc[i] = st.iloc[i - 1]

    col = f"STX_{atr_period}_{multiplier}"
    out[col] = st
    return out


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Transform an OHLCV frame into Heikin-Ashi candles (same schema)."""
    if df is None or df.empty or not {"open", "high", "low", "close"} <= set(df.columns):
        return df
    out = df.copy()
    o = out["open"].astype(float)
    h = out["high"].astype(float)
    lo = out["low"].astype(float)
    c = out["close"].astype(float)
    ha_close = (o + h + lo + c) / 4.0
    ha_open = o.copy()
    ha_open.iloc[0] = (o.iloc[0] + c.iloc[0]) / 2.0
    for i in range(1, len(out)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0
    out["open"] = ha_open
    out["high"] = pd.concat([h, ha_open, ha_close], axis=1).max(axis=1)
    out["low"] = pd.concat([lo, ha_open, ha_close], axis=1).min(axis=1)
    out["close"] = ha_close
    return out


def renko_bricks(df: pd.DataFrame, box_size: float = 7.0) -> pd.DataFrame:
    """Build fixed-box Renko bricks from a close series.

    Returns a frame of bricks with 'close' and 'direction' (1 up / -1 down);
    a new brick is minted when price moves `box_size` from the previous brick.
    """
    if df is None or df.empty or "close" not in df:
        return pd.DataFrame(columns=["timestamp", "close", "direction"])
    closes = df["close"].astype(float).tolist()
    stamps = df["timestamp"].tolist() if "timestamp" in df.columns else list(range(len(closes)))
    bricks = []
    prev = closes[0]
    direction = 0
    for ts, px in zip(stamps[1:], closes[1:]):
        if direction == 0:
            if px >= prev + box_size:
                direction = 1
            elif px <= prev - box_size:
                direction = -1
            else:
                continue
        elif px >= prev + box_size:
            direction = 1
        elif px <= prev - box_size:
            direction = -1
        else:
            continue
        bricks.append({"timestamp": ts, "close": px, "direction": direction})
        prev = px
    return pd.DataFrame(bricks)


# Register each computable indicator at import time. The registry is the
# contract (id + default params + plot shape); the calc lives in the fn above
# and is reused verbatim by compute_bundle. New indicator = register() one
# entry here — no call-site edits anywhere else.
indicator.register("rsi", IndicatorSpec(
    id="rsi", label="RSI", params={"rsi_period": 14},
    series=True, plot=PlotSpec(series_key="rsi_14", pane="separate", color="#7e57c2")))
indicator.register("adx", IndicatorSpec(
    id="adx", label="Average Directional Index (ADX)", params={"adx_period": 14},
    series=True, plot=PlotSpec(series_key="adx_14", pane="separate", color="#f59e0b")))
indicator.register("atr", IndicatorSpec(
    id="atr", label="ATR", params={"atr_period": 14},
    series=True, plot=PlotSpec(series_key="atr_14", pane="separate", color="#ff9800")))
indicator.register("sma", IndicatorSpec(
    id="sma", label="SMA", params={"sma_periods": (20,)},
    series=True, plot=PlotSpec(series_key="sma_20", pane="overlay", color="#2196f3")))
indicator.register("ema", IndicatorSpec(
    id="ema", label="EMA", params={"ema_periods": (9,)},
    series=True, plot=PlotSpec(series_key="ema_9", pane="overlay", color="#4caf50")))
indicator.register("vwap", IndicatorSpec(
    id="vwap", label="VWAP", params={},
    series=True, plot=PlotSpec(series_key="vwap", pane="overlay", color="#f44336")))
indicator.register("vwap_bands", IndicatorSpec(
    id="vwap_bands", label="VWAP Bands", params={"vwap_bands_std": 2.0},
    series=False, plot=PlotSpec(series_key="vwap_upper", pane="overlay", color="#9e9e9e")))
indicator.register("hma", IndicatorSpec(
    id="hma", label="HMA", params={"hma_period": 21},
    series=True, plot=PlotSpec(series_key="hma_21", pane="overlay", color="#00bcd4")))
indicator.register("st", IndicatorSpec(
    id="st", label="SuperTrend", params={"st_period": 10, "st_mult": 3.0},
    series=True, plot=PlotSpec(series_key="stx_10_3", pane="separate", color="#e91e63")))
# Chart overlays produced by OverlayPipeline (not compute_bundle): registered
# here so /api/market/catalog mirrors the FE's render ids (vwap already above).
# The contract is metadata-only; the calc lives in ntrade.analytics.overlay_pipeline.
indicator.register("halftrend", IndicatorSpec(
    id="halftrend", label="HalfTrend",
    params={"amplitude": 2, "channel_deviation": 2, "atr_period": 100},
    series=False, plot=PlotSpec(series_key="halftrend", pane="overlay", color="#4caf50")))
indicator.register("volume_profile", IndicatorSpec(
    id="volume_profile", label="Volume Profile", params={},
    series=True, plot=PlotSpec(series_key="volume_profile", pane="overlay", color="#ffb300")))
indicator.register("absorptions", IndicatorSpec(
    id="absorptions", label="Absorption Bars", params={"avg_volume_mult": 1.5, "range_threshold": 0.5},
    series=False, plot=PlotSpec(series_key="absorptions", pane="overlay", color="#ab47bc")))


# ---------------------------------------------------------------------------
# Registry-driven indicator compute. Each entry maps an indicator id to a
# computer that populates `result` for one candle frame. New indicator =
# `indicator.register(id, ...)` + `register_computer(id, fn)` in the same
# module — no edits to compute_bundle or any caller. `compute_bundle` iterates
# the registry instead of a hardcoded if-chain, so a registered indicator is
# actually computed (the old chain silently skipped unlisted ids).
# ---------------------------------------------------------------------------
_COMPUTERS: dict[str, typing.Callable] = {}


def register_computer(id: str, fn: typing.Callable) -> None:
    """Register a scalar computer for indicator `id`.

    `fn(df, params, result)` writes its scalar(s) into `result`, using the
    spec defaults from `params` and skipping gracefully when inputs are
    absent (e.g. hma only when `hma_period` is requested).
    """
    _COMPUTERS[id] = fn


def _last(series: pd.Series) -> float:
    return float(series.iloc[-1])


def _compute_rsi(df, params, result):
    p = int(params.get("rsi_period", 14))
    result[f"rsi_{p}"] = _last(rsi(df, p))


def _compute_adx(df, params, result):
    p = int(params.get("adx_period", 14))
    res = adx(df, p)
    if not res.empty and pd.notna(res["adx"].iloc[-1]):
        result[f"adx_{p}"] = float(res["adx"].iloc[-1])
        result[f"plus_di_{p}"] = float(res["plus_di"].iloc[-1])
        result[f"minus_di_{p}"] = float(res["minus_di"].iloc[-1])


def _compute_atr(df, params, result):
    p = int(params.get("atr_period", 14))
    result[f"atr_{p}"] = _last(atr(df, p))


def _compute_vwap(df, params, result):
    result["vwap"] = _last(vwap(df))


def _compute_hma(df, params, result):
    if not params.get("hma_period"):
        return
    p = int(params["hma_period"])
    result[f"hma_{p}"] = _last(hma(df, p))


def _compute_vwap_bands(df, params, result):
    if not params.get("vwap_bands_std"):
        return
    std = float(params["vwap_bands_std"])
    try:
        u, lo = vwap_bands(df, std)
        if pd.notna(u) and pd.notna(lo):
            result["vwap_upper"], result["vwap_lower"] = u, lo
    except Exception as exc:  # noqa: BLE001 — visibility, not silence (L1)
        logger.warning("vwap_bands failed on %d rows: %s", len(df), exc)


def _compute_ema(df, params, result):
    for p in params.get("ema_periods", (9, 21)):
        result[f"ema_{int(p)}"] = _last(ema(df, int(p)))


def _compute_sma(df, params, result):
    for p in params.get("sma_periods", ()):
        result[f"sma_{int(p)}"] = _last(sma(df, int(p)))


def _compute_st(df, params, result):
    period = int(params.get("st_period", 10))
    mult = float(params.get("st_mult", 3.0))
    # Normalize 3.0 -> 3 so the column name matches supertrend()'s
    # "STX_{period}_{multiplier}" format (multiplier=3.0 -> "STX_10_3").
    key_mult = int(mult) if mult == int(mult) else mult
    col = f"STX_{period}_{key_mult}"
    # supertrend returns "up"/"down" strings per bar — store the last value
    # as-is (not coerced to float) to match the original bundle contract.
    result[f"stx_{period}_{key_mult}"] = supertrend(df, period, key_mult)[col].iloc[-1]


def _compute_avg_volume(df, params, result):
    result["avg_volume"] = float(df["volume"].astype(float).mean())


for _id, _fn in (
    ("rsi", _compute_rsi), ("adx", _compute_adx), ("atr", _compute_atr), ("vwap", _compute_vwap),
    ("hma", _compute_hma), ("vwap_bands", _compute_vwap_bands),
    ("ema", _compute_ema), ("sma", _compute_sma), ("st", _compute_st),
    ("avg_volume", _compute_avg_volume),
):
    register_computer(_id, _fn)


# The default bundle mirrors the historical compute set so callers that don't
# name indicators still get the same scalars as before. hma and vwap_bands are
# param-gated (they self-skip unless their std/period is requested) but are
# always eligible — the original compute_bundle checked them on every call.
_DEFAULT_BUNDLE = ("rsi", "adx", "atr", "vwap", "hma", "vwap_bands", "ema", "sma", "st", "avg_volume")


def compute_bundle(df: pd.DataFrame, **params) -> dict[str, float]:
    """Compute an indicator bundle over the latest completed candle.

    Without ``indicators=`` it runs the default bundle (behavior-preserving).
    Pass ``indicators=(...)`` to compute a subset. Each id resolves through the
    computer registry, so a registered indicator is always actually computed.
    Data-dependent failures are logged and skipped (L1), never silently
    swallowed into a missing key.
    """
    if df is None or df.empty or "close" not in df:
        return {}
    ids = params.pop("indicators", None) or _DEFAULT_BUNDLE
    result: dict[str, float] = {}
    for id in ids:
        fn = _COMPUTERS.get(id)
        if fn is None:
            logger.debug("no computer registered for indicator %r", id)
            continue
        try:
            fn(df, params, result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("indicator %s failed on %d rows: %s", id, len(df), exc)
    return result
