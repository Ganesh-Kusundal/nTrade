"""Canonical interval/timeframe mappings — single source of truth.

Engine seconds, API bar-span minutes ("1D" = 375 min session bar), and
API/pump span minutes ("1D" = 1440 calendar minutes) are defined here.
Callers must import from this module, not re-declare local dicts.
"""
from __future__ import annotations

from ntrade.domain.constants import Timeframe

SESSION_BAR_MINUTES: int = 375

_BASE_SECONDS: dict[str, int] = {
    Timeframe.S1: 1,
    Timeframe.S5: 5,
    Timeframe.MIN: 60,
    Timeframe.T5: 300,
    Timeframe.T15: 900,
    Timeframe.H1: 3600,
    Timeframe.D1: 86400,
}

# Include both casings for "1d"/"1D" since the wire uses "1D" and Timeframe uses "1d".
TIMEFRAME_SECONDS: dict[str, int] = {**_BASE_SECONDS, "1D": 86400}

WIRE_INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "1h", "1D")

_BAR_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1D": 375}
_SPAN_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1D": 1440}

def normalize_interval(interval: str) -> str:
    s = str(interval)
    if s.lower() == "1d":
        return "1D"
    if s in _BAR_MINUTES and s != "1D":
        return s
    if s.lower() in {"1m","5m","15m","1h"}:
        return s.lower()
    raise ValueError(f"Unsupported interval {interval!r}; expected one of {WIRE_INTERVALS}")

def timeframe_seconds(timeframe: str) -> int:
    s = str(timeframe)
    if s in TIMEFRAME_SECONDS:
        return TIMEFRAME_SECONDS[s]
    if s.lower() in TIMEFRAME_SECONDS:
        return TIMEFRAME_SECONDS[s.lower()]
    raise ValueError(f"Unsupported timeframe {s!r}; expected one of {sorted(TIMEFRAME_SECONDS)}")

def interval_bar_minutes(interval: str) -> int:
    key = normalize_interval(interval)
    return _BAR_MINUTES[key]

def interval_span_minutes(interval: str) -> int:
    key = normalize_interval(interval)
    return _SPAN_MINUTES[key]

def interval_span_seconds(interval: str) -> int:
    return interval_span_minutes(interval) * 60
