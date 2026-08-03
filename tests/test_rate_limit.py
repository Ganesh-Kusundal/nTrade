"""Tests for BrokerRateGate / Quota / RateLimited / is_rate_limited.

All time-sensitive tests use a fake clock + fake sleep so they are
deterministic and zero wall-clock.
"""

from __future__ import annotations

import threading
import time

import pytest

from ntrade.execution.rate_limit import (
    BrokerRateGate, Quota, RateLimited, is_rate_limited,
)


class FakeClock:
    """Deterministic clock + sleep pair: sleep() advances the clock."""

    def __init__(self, t: float = 0.0):
        self.t = t
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


# ---------------------------------------------------------------- window math


class TestBrokerRateGateWindows:
    def test_first_acquire_is_immediate(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.acquire(Quota.QUOTE)
        assert clock.sleeps == []

    def test_quote_cannot_fire_twice_within_1s(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.acquire(Quota.QUOTE)          # t=0
        clock.t = 0.5
        gate.acquire(Quota.QUOTE)          # must block until t >= 1.0
        assert sum(clock.sleeps) >= 0.5
        assert clock.t >= 1.0

    def test_data_window_5_per_second(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        for _ in range(5):
            gate.acquire(Quota.DATA)       # 5 at t=0 pass
        assert clock.sleeps == []
        clock.t = 0.5
        gate.acquire(Quota.DATA)           # 6th at t=0.5 blocks until t>=1
        assert sum(clock.sleeps) >= 0.5
        assert clock.t >= 1.0

    def test_order_windows_10_per_second(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        for _ in range(10):
            gate.acquire(Quota.ORDER)
        assert clock.sleeps == []
        clock.t = 0.1
        gate.acquire(Quota.ORDER)          # 11th within the same second blocks
        assert sum(clock.sleeps) >= 0.9

    def test_order_windows_config_has_minute_hour_day(self):
        """ORDER must carry the full 10/s, 250/min, 1000/h, 7000/day shape."""
        gate = BrokerRateGate()
        spans = [span for span, _ in gate._windows[Quota.ORDER]]
        limits = [limit for _, limit in gate._windows[Quota.ORDER]]
        assert spans == [1.0, 60.0, 3600.0, 86400.0]
        assert limits == [10, 250, 1000, 7000]

    def test_penalize_backs_off_quota(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.penalize(Quota.QUOTE, 5.0)
        gate.acquire(Quota.QUOTE)          # window clear, but cooldown active
        assert sum(clock.sleeps) >= 5.0
        assert clock.t >= 5.0

    def test_penalize_is_class_scoped(self):
        """A DATA penalty must not block QUOTE traffic."""
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.penalize(Quota.DATA, 10.0)
        gate.acquire(Quota.QUOTE)          # unaffected
        assert clock.sleeps == []

    def test_injectable_clock_and_sleep_used(self):
        """With injected clock/sleep, real time.sleep is never called."""
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.acquire(Quota.QUOTE)
        clock.t = 0.5
        gate.acquire(Quota.QUOTE)
        assert clock.sleeps == [0.5]       # our fake sleep, not time.sleep


# ---------------------------------------------------------------- threading


class TestBrokerRateGateThreads:
    def test_shared_gate_serializes_threads(self):
        """N threads sharing one gate never exceed the class window: total wall
        time must be >= (N*M - 1)/rate (loose bound). Uses a fast fake window
        (100/s) so the test stays fast while still asserting shared throttling.
        """
        from ntrade.execution.rate_limit import DEFAULT_WINDOWS
        windows = dict(DEFAULT_WINDOWS)
        windows[Quota.QUOTE] = ((0.01, 1),)   # 100/s
        gate = BrokerRateGate(windows=windows)
        n_threads, per_thread = 4, 10
        t0 = time.monotonic()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(per_thread):
                    gate.acquire(Quota.QUOTE)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.monotonic() - t0
        assert not errors
        assert elapsed >= (n_threads * per_thread - 1) * 0.01 * 0.5


# ---------------------------------------------------------------- is_rate_limited


class TestIsRateLimited:
    def test_rate_limited_instance(self):
        assert is_rate_limited(RateLimited(Quota.QUOTE))
        assert is_rate_limited(RateLimited(Quota.ORDER, retry_after=2.0))

    def test_dh904_text(self):
        assert is_rate_limited(RuntimeError("DH-904 rate limit exceeded"))
        assert is_rate_limited(ValueError("error_code: DH-904"))

    def test_rate_limit_variants(self):
        assert is_rate_limited(RuntimeError("Rate_Limit"))
        assert is_rate_limited(RuntimeError("rate limit reached"))
        assert is_rate_limited(RuntimeError("HTTP 429 Too Many Requests"))

    def test_non_rate_limit_is_false(self):
        assert not is_rate_limited(ValueError("LTP is 0"))
        assert not is_rate_limited(RuntimeError("invalid token DH-906"))
        assert not is_rate_limited(ConnectionError("network down"))

    def test_rate_limited_fields(self):
        exc = RateLimited(Quota.DATA, retry_after=3.5, message="slow down")
        assert exc.quota is Quota.DATA
        assert exc.retry_after == 3.5
        assert "slow down" in str(exc)
        assert str(RateLimited(Quota.QUOTE)) == "rate limited on quote"


# ---------------------------------------------------------------- telemetry (T-036)


class TestGateStatus:
    def test_status_reports_empty_windows(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        snap = gate.status()
        assert set(snap) == {"quote", "data", "order", "non_trading"}
        for info in snap.values():
            assert not info["blocked"]
            assert info["cooldown_remaining"] == 0
        # order class has 4 windows (1s/60s/1h/1d), quote has 1
        assert len(snap["order"]["windows"]) == 4
        assert len(snap["quote"]["windows"]) == 1

    def test_status_reflects_used_tokens(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.acquire(Quota.QUOTE)
        gate.acquire(Quota.DATA)
        gate.acquire(Quota.DATA)
        snap = gate.status()
        assert snap["quote"]["windows"][0]["used"] == 1
        assert snap["quote"]["windows"][0]["limit"] == 1
        # A full 1s burst window is normal steady state, not exhaustion
        # (T-036: the login probe fills quote=1/1 right before the snapshot).
        assert snap["quote"]["blocked"] is False      # 1/1 burst, not blocked
        assert snap["data"]["windows"][0]["used"] == 2
        assert snap["data"]["blocked"] is False      # 2/5 still free

    def test_status_full_long_window_is_blocked(self):
        """A sustained window (>= BLOCKED_MIN_WINDOW_SPAN_S) at capacity IS
        blocked — genuine exhaustion a fresh acquire would wait on."""
        from ntrade.execution.rate_limit import BLOCKED_MIN_WINDOW_SPAN_S
        clock = FakeClock()
        # Order class: fire all 250 acquires at t=0 (both windows at limit
        # 250; the 250th acquire sees len=249 < 250 and passes). The 60s
        # window then holds 250/250 and is the binding long-horizon window.
        windows = {Quota.ORDER: ((1.0, 250), (BLOCKED_MIN_WINDOW_SPAN_S, 250))}
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep, windows=windows)
        for _ in range(250):
            gate.acquire(Quota.ORDER)
        snap = gate.status()
        assert snap["order"]["blocked"] is True       # 60s window exhausted
        assert snap["quote"]["blocked"] is False      # untouched classes stay clear

    def test_status_reflects_penalty_cooldown(self):
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.penalize(Quota.DATA, 3.0)
        snap = gate.status()
        assert snap["data"]["cooldown_remaining"] == 3.0
        assert snap["data"]["blocked"] is True
        assert snap["quote"]["blocked"] is False     # class-scoped

    def test_status_is_read_only(self):
        """status() must never mutate gate state (no token accounting drift)."""
        clock = FakeClock()
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
        gate.acquire(Quota.QUOTE)
        before = len(gate._history[Quota.QUOTE][0])
        gate.status()
        gate.status()
        assert len(gate._history[Quota.QUOTE][0]) == before
