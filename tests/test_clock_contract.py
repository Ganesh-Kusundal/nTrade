"""P0: enforce the clock abstraction contract.

LiveClock must return IST-aware datetime. Any strategy/engine calling
datetime.now() directly (instead of via clock.now()) silently breaks
zero-parity — and on a non-IST host, produces wrong market-session decisions.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from ntrade.kernel.clock import LiveClock, ReplayClock, SimulationClock

IST = ZoneInfo("Asia/Kolkata")


def test_live_clock_returns_ist_aware_datetime():
    """LiveClock.now() must be tz-aware and resolve to IST."""
    clock = LiveClock()
    now = clock.now()
    assert now.tzinfo is not None, "LiveClock.now() returned naive datetime"
    assert now.tzinfo == IST, f"expected IST, got {now.tzinfo}"


def test_live_callable_returns_now():
    clock = LiveClock()
    result = clock()
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_replay_clock_deterministic():
    clock = ReplayClock()
    assert clock.now() == datetime(1970, 1, 1, tzinfo=IST)
    clock.set(datetime(2026, 8, 14, 9, 15, tzinfo=IST))
    assert clock.now() == datetime(2026, 8, 14, 9, 15, tzinfo=IST)


def test_simulation_clock_inherits_replay():
    clock = SimulationClock(speed=10.0)
    assert clock.now() == datetime(1970, 1, 1, tzinfo=IST)
    clock.advance(minutes=5)
    assert clock.now() == datetime(1970, 1, 1, 0, 5, tzinfo=IST)
