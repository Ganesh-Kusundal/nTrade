"""TradingClock — the single source of time for the trading kernel.

Engines and strategies never call ``datetime.now()`` directly; they ask the
clock. ``LiveClock`` is wall time; ``ReplayClock``/``SimulationClock`` are
deterministic, so the same event stream produces the same decisions in live,
replay and backtest (zero parity).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ntrade.domain.market_hours import IST as _IST


class TradingClock:
    """Base clock contract. Subclasses define the time source."""

    def now(self) -> datetime:
        raise NotImplementedError

    def __call__(self) -> datetime:
        return self.now()


class LiveClock(TradingClock):
    """Wall-clock time — used in live trading. Always returns IST-aware datetime."""

    def now(self) -> datetime:
        return datetime.now(tz=_IST)


class ReplayClock(TradingClock):
    """Deterministic clock driven by the events being replayed."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(1970, 1, 1, tzinfo=_IST)

    def now(self) -> datetime:
        return self._now

    def set(self, ts: datetime) -> None:
        """Jump the clock to a specific timestamp (e.g. the next event's ts)."""
        self._now = ts

    def advance(self, **kwargs) -> None:
        """Advance by timedelta kwargs (minutes=5, seconds=1, ...)."""
        self._now += timedelta(**kwargs)


class SimulationClock(ReplayClock):
    """A ReplayClock that can also scale time (speed factor for backtests)."""

    def __init__(self, start: datetime | None = None, speed: float = 1.0):
        super().__init__(start)
        self.speed = float(speed)
