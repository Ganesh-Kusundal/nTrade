"""NSE / MCX session hours (IST) — shared by domain strategies and the API.

The same schedule powers the ValentiniScalper session gate, the live-feed
gater, and the frontend's ``strategySession`` — a single source of truth so
MCX evening-session contracts trade their full 09:00–23:25 window.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE cash + NFO index/stock futures: 09:15 – 15:30 IST
_NSE_OPEN, _NSE_CLOSE = time(9, 15), time(15, 30)
# MCX non-agri (crude/gold/silver) evening session: 09:00 – 23:30 IST
_MCX_OPEN, _MCX_CLOSE = time(9, 0), time(23, 30)


def session_open(exchange: str) -> time:
    """Opening time (IST) for the given exchange segment."""
    return _MCX_OPEN if str(exchange).upper() == "MCX" else _NSE_OPEN


def session_close(exchange: str) -> time:
    """Closing time (IST) for the given exchange segment."""
    return _MCX_CLOSE if str(exchange).upper() == "MCX" else _NSE_CLOSE


def is_market_open(exchange: str, now: datetime | None = None) -> bool:
    """True when ``exchange`` is inside its weekday session (IST)."""
    now = now or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    t = now.time()
    op = session_open(exchange)
    cl = session_close(exchange)
    return op <= t < cl
