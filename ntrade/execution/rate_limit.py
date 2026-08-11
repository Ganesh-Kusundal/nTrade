"""BrokerRateGate — multi-window, multi-class broker API rate limiting.

The single choke point every outbound broker REST call passes through. Each
quota class (matching Dhan's documented rate-limit table) enforces its own
sliding windows; ``acquire`` blocks until every window for the class has
capacity, ``penalize`` backs a class off after a DH-904 so a rejected call does
not immediately re-fire into the same quota.

Design notes:
  - Windows are real sliding windows (deque of timestamps + a per-class
    cooldown), NOT a min-interval spacer: a 1/s class and a 10/s class coexist
    in one gate without a shared global tick.
  - The wait is computed under the lock but the sleep happens OUTSIDE it, so a
    slow class never head-of-line-blocks other classes behind the lock.
  - ``clock`` and ``sleep`` are injectable (defaults ``time.monotonic`` /
    ``time.sleep``) so tests are deterministic and zero wall-clock.
  - Thread-safe: all state is guarded by a single ``threading.Lock``.

Stdlib-only; no third-party dependencies.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from enum import Enum
from typing import Callable


class Quota(Enum):
    """Rate-limit quota classes matching Dhan's documented API buckets."""

    QUOTE = "quote"               # 1/s
    DATA = "data"                 # 5/s, 100_000/day
    ORDER = "order"               # 10/s, 250/min, 1000/h, 7000/day
    NON_TRADING = "non_trading"   # 20/s


# (span_seconds, limit) per class — Dhan Rate Limit table defaults
# (https://dhanhq.co/docs/v2/ — Order / Data / Quote / Non Trading buckets).
DEFAULT_WINDOWS: dict[Quota, tuple[tuple[float, int], ...]] = {
    Quota.QUOTE: ((1.0, 1),),
    Quota.DATA: ((1.0, 5), (86400.0, 100_000)),
    Quota.ORDER: ((1.0, 10), (60.0, 250), (3600.0, 1000), (86400.0, 7000)),
    Quota.NON_TRADING: ((1.0, 20),),
}

# A quota class reports ``blocked`` (status telemetry, T-036) only when a
# fresh acquire would wait a *material* time: an active DH-904 cooldown, or a
# window with span >= this many seconds at capacity. A momentarily-full burst
# window (1s/5s shaping) is normal steady-state operation — it frees within
# seconds — so it must not flag the class: without this, the pre-flight quota
# row would be DEGRADED after the connect-time login probe consumes the single
# 1/s QUOTE slot (the rate_gate false-positive).
BLOCKED_MIN_WINDOW_SPAN_S = 60.0


class RateLimited(RuntimeError):
    """Raised when Dhan rejects a call with DH-904 / Rate_Limit.

    ``retry_after`` is the server-suggested backoff when known; the gate uses
    it to ``penalize`` the class so the next acquire waits before re-firing.
    """

    def __init__(self, quota: Quota, retry_after: float | None = None,
                 message: str = ""):
        self.quota = quota
        self.retry_after = retry_after
        super().__init__(message or f"rate limited on {quota.value}")


def is_rate_limited(exc: Exception) -> bool:
    """True when *exc* is a :class:`RateLimited` or its text matches Dhan's
    rate-limit signals (DH-904 / Rate_Limit / 'rate limit' / HTTP 429 /
    'too many requests')."""
    if isinstance(exc, RateLimited):
        return True
    text = f"{exc}".lower()
    return any(marker in text for marker in (
        "dh-904", "rate_limit", "rate limit", "429", "too many requests",
    ))


class BrokerRateGate:
    """Thread-safe multi-window rate gate for one broker session.

    Usage::

        gate = BrokerRateGate()                  # Dhan default windows
        gate.acquire(Quota.QUOTE)                # block until a QUOTE slot frees
        try:
            data = tsl.get_ltp_data(names=[sym])
        except RateLimited as exc:
            gate.penalize(Quota.QUOTE, exc.retry_after or 1.0)
            raise
    """

    def __init__(self, clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], None] | None = None,
                 windows: dict | None = None):
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        # A partial override merges over the Dhan defaults; unknown classes
        # keep their default windows (never KeyError at construction).
        self._windows = {**DEFAULT_WINDOWS, **(windows or {})}
        self._lock = threading.Lock()
        self._history: dict[Quota, list[deque]] = {
            q: [deque() for _ in self._windows[q]] for q in Quota
        }
        self._cooldown_until: dict[Quota, float] = {q: 0.0 for q in Quota}

    # ------------------------------------------------------------------ core
    def acquire(self, quota: Quota) -> None:
        """Block until every window for *quota* has capacity.

        Computes the longest wait across all of the class's windows under the
        lock, releases it, sleeps *outside* the lock, then re-checks — so one
        slow class never blocks other classes on the lock itself.
        """
        while True:
            with self._lock:
                wait = self._compute_wait(quota)
                if wait <= 0:
                    now = self._clock()
                    for window in self._history[quota]:
                        window.append(now)
                    return
            self._sleep(wait)

    def penalize(self, quota: Quota, seconds: float) -> None:
        """Back off *quota* for *seconds* after a DH-904.

        The next ``acquire`` on this class waits at least *seconds* (plus any
        window contention) even if the class's own windows are otherwise clear.
        """
        with self._lock:
            now = self._clock()
            self._cooldown_until[quota] = max(self._cooldown_until[quota], now + seconds)

    # ------------------------------------------------------------------ telemetry
    def status(self) -> dict:
        """Read-only snapshot of every quota class (T-036).

        Returns ``{quota.value: {"windows": [{span_s, limit, used}],
        "cooldown_remaining": float, "blocked": bool}}`` — used for the
        pre-deploy quota-headroom report and operator dashboards. Never blocks
        and never mutates gate state (windows are not pruned here).

        ``blocked`` means a fresh acquire would wait a material time: an
        active DH-904 cooldown, or a long-horizon window (``span_s >=
        BLOCKED_MIN_WINDOW_SPAN_S``) at capacity. A momentarily-full burst
        window (1s/5s shaping) is normal operation and is NOT blocked —
        otherwise the pre-flight quota row reports DEGRADED after any single
        quote call (T-036 regression).
        """
        now = self._clock()
        out: dict = {}
        with self._lock:
            for quota in Quota:
                windows = []
                for (span, limit), window in zip(self._windows[quota], self._history[quota]):
                    # count entries still inside this window (no pruning)
                    used = sum(1 for t in window if t > now - span)
                    windows.append({"span_s": span, "limit": limit, "used": used})
                cooldown = max(0.0, self._cooldown_until[quota] - now)
                blocked = cooldown > 0 or any(
                    w["used"] >= w["limit"]
                    and w["span_s"] >= BLOCKED_MIN_WINDOW_SPAN_S
                    for w in windows
                )
                out[quota.value] = {
                    "windows": windows,
                    "cooldown_remaining": round(cooldown, 3),
                    "blocked": blocked,
                }
        return out

    # ------------------------------------------------------------------ internals
    def _compute_wait(self, quota: Quota) -> float:
        """Longest wait (seconds) before *quota* can fire, under the lock."""
        now = self._clock()
        cooldown = self._cooldown_until[quota] - now
        if cooldown > 0:
            return cooldown
        wait = 0.0
        for (span, limit), window in zip(self._windows[quota], self._history[quota]):
            while window and window[0] <= now - span:
                window.popleft()
            if len(window) >= limit:
                # oldest entry leaves the window at oldest + span
                wait = max(wait, window[0] + span - now)
        return wait
