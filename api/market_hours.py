"""NSE / MCX session clocks (IST). Weekends closed; holidays ignored (ponytail).

Delegates to ``ntrade.domain.market_hours`` — the single source of truth for
exchange session hours, shared with the ValentiniScalper strategy gate and
the frontend's ``strategySession``.
"""
from __future__ import annotations

from datetime import datetime, time

from ntrade.domain.market_hours import (
    IST,
    is_market_open,
    session_close as _close,
    session_open as _open,
)

# Re-export the canonical schedule.
session_open = _open
session_close = _close
