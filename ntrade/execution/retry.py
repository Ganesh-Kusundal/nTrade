"""RetryPolicy and RateLimiter — configurable resilience infrastructure.

Provides:
  - RetryPolicy: frozen dataclass with exponential backoff + jitter
  - RateLimiter: thread-safe token-bucket rate limiter

Both are stdlib-only and designed for the hot path (broker API calls).

The broker rate-limit gate (BrokerRateGate / Quota / RateLimited) lives in
``ntrade.execution.rate_limit`` and is re-exported here for convenience — a
``RetryPolicy`` never retries a rate-limit failure (DH-904) by default, since
retrying a throttled call only amplifies the quota exhaustion.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generator

from ntrade.domain.constants import RETRY_BASE_DELAY_S, RETRY_MAX_DELAY_S, RETRY_MULTIPLIER_S, RETRY_JITTER_S, RETRY_MAX_RETRIES
from ntrade.execution.rate_limit import BrokerRateGate, Quota, RateLimited, is_rate_limited

__all__ = [
    "RetryPolicy", "RateLimiter",
    "BrokerRateGate", "Quota", "RateLimited", "is_rate_limited",
]


@dataclass(frozen=True)
class RetryPolicy:
    """Configurable retry policy with exponential backoff.

    Usage::

        policy = RetryPolicy(max_retries=3, base_delay=0.2)
        result = policy.execute(some_flaky_call)

    The *execute* method retries *fn* on any exception **except** rate-limit
    failures (:class:`RateLimited` / anything ``is_rate_limited`` matches),
    which are re-raised immediately — retrying a DH-904 only burns more quota.
    Between attempts it sleeps for an exponentially increasing delay (capped at
    *max_delay*) with a small random jitter to de-synchronise concurrent
    callers.

    *no_retry_on* overrides the default rate-limit guard with a custom
    predicate; returning True for an exception re-raises it immediately.
    """

    max_retries: int = RETRY_MAX_RETRIES
    base_delay: float = RETRY_BASE_DELAY_S       # seconds
    max_delay: float = RETRY_MAX_DELAY_S         # cap on exponential backoff
    multiplier: float = RETRY_MULTIPLIER_S       # backoff multiplier
    jitter: float = RETRY_JITTER_S               # random jitter range (+-jitter/2)
    no_retry_on: Callable[[Exception], bool] | None = field(
        default=None, repr=False, compare=False,
    )

    # -- core API -----------------------------------------------------------

    def execute(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute *fn* with retry on failure.

        Returns *fn*'s result on success.  If all attempts raise, the last
        exception is re-raised.  Rate-limit failures are never retried — they
        are re-raised on the first attempt (B-011).
        """
        delay_iter = iter(self.delays())
        last_exc: BaseException | None = None
        for attempt in range(self.max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if self._is_no_retry(exc):
                    raise
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(next(delay_iter))
        raise last_exc  # type: ignore[misc]

    def _is_no_retry(self, exc: Exception) -> bool:
        """True when this exception must be re-raised immediately (never retried)."""
        if self.no_retry_on is not None:
            return bool(self.no_retry_on(exc))
        return is_rate_limited(exc)

    def delays(self) -> Generator[float, None, None]:
        """Yield delay values for each retry attempt.

        Produces *max_retries* values (the caller is expected to consume at
        most *max_retries - 1*, but the extra value is harmless).
        """
        for i in range(self.max_retries):
            delay = min(self.base_delay * (self.multiplier ** i), self.max_delay)
            jitter_val = random.uniform(-self.jitter / 2, self.jitter / 2)
            yield max(0.0, delay + jitter_val)


class RateLimiter:
    """Simple token-bucket rate limiter (thread-safe).

    Usage::

        limiter = RateLimiter(calls_per_second=10)
        limiter.wait()   # blocks if needed
        make_api_call()

    The bucket starts full (one token available immediately).  Each call to
    :meth:`wait` consumes one token; if the bucket is empty the caller blocks
    until a token is replenished.
    """

    def __init__(self, calls_per_second: float = 10.0) -> None:
        self._rate = calls_per_second
        self._interval = 1.0 / calls_per_second if calls_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._last_time: float = 0.0

    def wait(self) -> None:
        """Block until the next call is allowed."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_time = time.monotonic()
