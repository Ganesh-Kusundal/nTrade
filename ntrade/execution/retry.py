"""RetryPolicy and RateLimiter — configurable resilience infrastructure.

Provides:
  - RetryPolicy: frozen dataclass with exponential backoff + jitter
  - RateLimiter: thread-safe token-bucket rate limiter

Both are stdlib-only and designed for the hot path (broker API calls).
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Generator


@dataclass(frozen=True)
class RetryPolicy:
    """Configurable retry policy with exponential backoff.

    Usage::

        policy = RetryPolicy(max_retries=3, base_delay=0.2)
        result = policy.execute(some_flaky_call)

    The *execute* method retries *fn* on any exception.  Between attempts it
    sleeps for an exponentially increasing delay (capped at *max_delay*) with
    a small random jitter to de-synchronise concurrent callers.
    """

    max_retries: int = 3
    base_delay: float = 0.2       # seconds
    max_delay: float = 5.0        # cap on exponential backoff
    multiplier: float = 2.0       # backoff multiplier
    jitter: float = 0.1           # random jitter range (+-jitter/2)

    # -- core API -----------------------------------------------------------

    def execute(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute *fn* with retry on failure.

        Returns *fn*'s result on success.  If all attempts raise, the last
        exception is re-raised.
        """
        delay_iter = iter(self.delays())
        last_exc: BaseException | None = None
        for attempt in range(self.max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(next(delay_iter))
        raise last_exc  # type: ignore[misc]

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
