"""Indicator bundle — pure functions over OHLCV dataframes.

No external TA library required; keeps the domain layer dependency-free.
"""

from __future__ import annotations

import logging

import pandas as pd

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
    l = out["low"].astype(float)
    c = out["close"].astype(float)
    ha_close = (o + h + l + c) / 4.0
    ha_open = o.copy()
    ha_open.iloc[0] = (o.iloc[0] + c.iloc[0]) / 2.0
    for i in range(1, len(out)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0
    out["open"] = ha_open
    out["high"] = pd.concat([h, ha_open, ha_close], axis=1).max(axis=1)
    out["low"] = pd.concat([l, ha_open, ha_close], axis=1).min(axis=1)
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


def compute_bundle(df: pd.DataFrame, **params) -> dict[str, float]:
    """Compute a standard indicator bundle over the latest completed candle."""
    if df is None or df.empty or "close" not in df:
        return {}
    rsi_period = int(params.get("rsi_period", 14))
    atr_period = int(params.get("atr_period", 14))
    st_period = int(params.get("st_period", 10))
    st_mult = float(params.get("st_mult", 3.0))
    st_key_mult = int(st_mult) if st_mult == int(st_mult) else st_mult
    ema_periods = params.get("ema_periods", (9, 21))
    sma_periods = params.get("sma_periods", ())
    result: dict[str, float] = {}
    def _capture(name: str, fn, *, store_key: str | None = None):
        """Compute one indicator; log+skip instead of silently swallowing so a
        data-dependent failure is visible (L1) rather than a quiet missing key."""
        try:
            value = fn()
            if value is not None and not pd.isna(value):
                result[store_key or name] = value
        except Exception as exc:  # noqa: BLE001
            logger.warning("indicator %s failed on %d rows: %s",
                           name, len(df), exc)

    def _series_last(name: str, series_fn, *, store_key: str | None = None):
        def _run():
            s = series_fn()
            v = s.iloc[-1]
            return float(v)
        _capture(name, _run, store_key=store_key)

    _series_last("rsi", lambda: rsi(df, rsi_period), store_key=f"rsi_{rsi_period}")
    _series_last("atr", lambda: atr(df, atr_period), store_key=f"atr_{atr_period}")
    _series_last("vwap", lambda: vwap(df), store_key="vwap")
    _capture("avg_volume", lambda: float(df["volume"].astype(float).mean()))

    def _supertrend_last():
        st = supertrend(df, st_period, st_mult)
        return st[f"STX_{st_period}_{st_mult}"].iloc[-1]
    _capture("supertrend", _supertrend_last, store_key=f"stx_{st_period}_{st_key_mult}")

    for period in ema_periods:
        _series_last(f"ema{int(period)}", lambda p=period: ema(df, int(p)),
                     store_key=f"ema_{int(period)}")
    for period in sma_periods:
        _series_last(f"sma{int(period)}", lambda p=period: sma(df, int(p)),
                     store_key=f"sma_{int(period)}")
    return result
