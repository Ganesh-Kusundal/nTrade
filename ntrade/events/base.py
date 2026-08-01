"""Canonical event model — the lingua franca of the trading kernel.

Every subsystem communicates by publishing/consuming events. Events are frozen
dataclasses (immutable, hashable) so they can be recorded, replayed and
compared deterministically. `ts` always comes from the TradingClock, never
`datetime.now()` — that is what makes replay/backtest/live identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base event. All market events carry a kernel-clock timestamp."""

    ts: datetime
    event_id: str = field(default_factory=lambda: uuid4().hex[:12])
