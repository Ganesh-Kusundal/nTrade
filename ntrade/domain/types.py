"""Timestamp semantics — single source of truth for naive-datetime meaning.

A naive datetime in this codebase is ALWAYS either:
  - NaiveIST: wall-clock IST (no tzinfo, interpreted as Asia/Kolkata)
  - NaiveUTC: wall-clock UTC (no tzinfo, interpreted as UTC)

Never pass a bare datetime across a layer boundary without wrapping it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ntrade.domain.market_hours import IST as _IST

_UTC = timezone.utc


class NaiveIST(datetime):
    """Marker subclass: this naive datetime is IST wall time."""

    __slots__ = ()


class NaiveUTC(datetime):
    """Marker subclass: this naive datetime is UTC wall time."""

    __slots__ = ()


def is_naive(ts: datetime) -> bool:
    return ts.tzinfo is None


def assert_naive_ist(ts: datetime) -> None:
    if ts.tzinfo is not None:
        raise ValueError(f"expected naive IST datetime, got aware {ts!r}")


def assert_naive_utc(ts: datetime) -> None:
    if ts.tzinfo is not None:
        raise ValueError(f"expected naive UTC datetime, got aware {ts!r}")


def to_naive_ist(ts: datetime) -> NaiveIST:
    """Strip any tzinfo, return as IST wall time (no conversion)."""
    naive = ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
    return NaiveIST(naive.year, naive.month, naive.day,
                    naive.hour, naive.minute, naive.second, naive.microsecond)


def to_utc(ts: datetime) -> NaiveUTC:
    """Convert naive IST → NaiveUTC."""
    aware_ist = ts.replace(tzinfo=_IST)
    utc = aware_ist.astimezone(_UTC).replace(tzinfo=None)
    return NaiveUTC(utc.year, utc.month, utc.day,
                    utc.hour, utc.minute, utc.second, utc.microsecond)


def to_ist(ts: datetime) -> NaiveIST:
    """Convert aware or naive-UTC → NaiveIST."""
    if ts.tzinfo is not None:
        converted = ts.astimezone(_IST).replace(tzinfo=None)
    else:
        aware = ts.replace(tzinfo=_UTC)
        converted = aware.astimezone(_IST).replace(tzinfo=None)
    return NaiveIST(converted.year, converted.month, converted.day,
                    converted.hour, converted.minute, converted.second,
                    converted.microsecond)


def strip_tz(ts):
    """Strip tz from a pandas Series or pd.Timestamp, returning naive wall time.

    Replaces the 2-line idiom:
        if getattr(s.dt, "tz", None) is not None:
            s = s.dt.tz_localize(None)
    Also handles pd.Timestamp scalars (via .tz_localize) and None.
    No pandas import at module level — safe for domain-layer imports.
    """
    if ts is None:
        return None
    dt_acc = getattr(ts, "dt", None)
    if dt_acc is not None:
        if getattr(dt_acc, "tz", None) is not None:
            return dt_acc.tz_localize(None)  # type: ignore[union-attr]
        return ts
    if getattr(ts, "tzinfo", None) is not None and hasattr(ts, "tz_localize"):
        return ts.tz_localize(None)
    return ts
