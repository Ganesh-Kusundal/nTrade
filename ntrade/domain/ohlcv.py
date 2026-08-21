"""OHLCV bar construction — single source of truth for resample/aggregation."""
from __future__ import annotations

import pandas as pd

RESAMPLE_AGG: dict[str, str] = {
    "open": "first", "high": "max", "low": "min",
    "close": "last", "volume": "sum",
}
BAR_LABEL_POLICY: dict[str, str] = {"closed": "left", "label": "right"}


def _day_origin(day, origin: str) -> pd.Timestamp:
    """Build a per-day origin Timestamp from an ``HH:MM`` string."""
    h, m = origin.split(":")
    return pd.Timestamp(day) + pd.Timedelta(hours=int(h), minutes=int(m))


def resample(
    df: pd.DataFrame,
    rule: str,
    *,
    origin: str = "09:15",
    day_group: bool = True,
    tz_strip: bool = True,
    **kw,
) -> pd.DataFrame:
    """Resample tick/1m bars to a higher timeframe using the canonical policy.

    Canonical OHLCV aggregation and bar-label conventions are fixed:
    ``RESAMPLE_AGG`` (open=first, high=max, low=min, close=last, volume/oi=sum)
    and ``BAR_LABEL_POLICY`` (closed="left", label="right").

    With ``day_group=True`` (the F-001 / dhan_mapper convention) the resample is
    anchored per calendar day at the ``origin`` time so night-session candles
    stay inside their own day. With ``day_group=False`` (the HistoricalSeries
    convention) it is a single epoch-anchored resample.
    """
    if df is None or df.empty or "timestamp" not in df:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce")
    if tz_strip and getattr(out["timestamp"].dt, "tz", None) is not None:
        out["timestamp"] = out["timestamp"].dt.tz_localize(None)
    out = out.dropna(subset=["timestamp"])
    if out.empty:
        return out.reset_index(drop=True)
    indexed = out.set_index("timestamp")
    agg = dict(RESAMPLE_AGG)
    if "oi" in indexed.columns:
        agg["oi"] = "sum"
    extra = {k: v for k, v in kw.items() if k not in ("closed", "label")}
    if not day_group:
        return (
            indexed
            .resample(rule, closed="left", label="right", **extra)
            .agg(agg)
            .dropna(subset=["open"])
            .reset_index()
        )
    rows = []
    for day, group in indexed.groupby(indexed.index.date):
        resampled = (
            group
            .resample(
                rule,
                origin=_day_origin(day, origin),
                closed="left",
                label="right",
                **extra,
            )
            .agg(agg)
            .dropna(subset=["open"])
        )
        rows.append(resampled)
    if not rows:
        return pd.DataFrame(columns=out.columns)
    return pd.concat(rows).reset_index()


def aggregate_daily(df: pd.DataFrame, *, origin: str = "09:15") -> pd.DataFrame:
    """Aggregate intraday bars to daily OHLCV (calendar-day bins)."""
    return resample(df, "1D", origin=origin, day_group=False)


def ohlcv_bundle() -> dict[str, str]:
    return dict(RESAMPLE_AGG)


try:
    from ntrade.domain.coercion import to_float, to_int  # noqa: F401
except ImportError:
    def to_float(v) -> float:
        try:
            return float(v or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def to_int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
