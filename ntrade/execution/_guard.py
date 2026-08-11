"""Idempotency guard + circuit breaker — inlined from v3 patterns (N6/D-22).

These are minimal, self-contained implementations that nTrade owns directly.
No dependency on tradex_v3 — the v3 implementations are referenced as
the canonical pattern but re-implemented here to keep nTrade self-contained.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from contextlib import contextmanager
from typing import Any


class IdempotencyDuplicate:
    """A completed idempotent request — its recorded result is returned."""

    __slots__ = ("result",)

    def __init__(self, result: Any) -> None:
        self.result = result


@dataclass(frozen=True, slots=True)
class _CorrelationId:
    """Identity for idempotency dedupe. Accepts any hashable value."""
    value: Any


class MemoryIdempotencyGuard:
    """In-process correlation-id dedupe with reservation + release.

    ``_completed`` is bounded to ``max_entries`` to avoid unbounded memory
    growth in long-running live processes. When the cap is hit, the oldest
    entries are evicted (FIFO, Python 3.7+ dict preserves insertion order).
    """

    def __init__(self, *, max_entries: int = 50_000) -> None:
        self._reserved: set[Any] = set()
        self._completed: dict[Any, Any] = {}
        self._insertion_order: list[Any] = []
        self._max_entries = max_entries
        self._lock = threading.RLock()

    def check_and_reserve(self, correlation_id) -> IdempotencyDuplicate | None:
        key = correlation_id.value if hasattr(correlation_id, "value") else correlation_id
        with self._lock:
            if key in self._completed:
                return IdempotencyDuplicate(result=self._completed[key])
            if key in self._reserved:
                raise RuntimeError(f"idempotency key is already reserved: {key}")
            self._reserved.add(key)
            return None

    def record_result(self, correlation_id, result: Any) -> None:
        key = correlation_id.value if hasattr(correlation_id, "value") else correlation_id
        with self._lock:
            if key not in self._completed:
                self._completed[key] = result
                self._insertion_order.append(key)
            self._reserved.discard(key)
            self._evict_if_over_cap()

    def release(self, correlation_id) -> None:
        key = correlation_id.value if hasattr(correlation_id, "value") else correlation_id
        with self._lock:
            self._reserved.discard(key)

    def _evict_if_over_cap(self) -> None:
        while len(self._insertion_order) > self._max_entries:
            oldest = self._insertion_order.pop(0)
            self._completed.pop(oldest, None)


# ---------------------------------------------------------------- circuit breaker


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0
    half_open_max: int = 1


class CircuitBreaker:
    """Fail-fast circuit breaker for broker health tracking.

    Tracks consecutive failures; when the threshold is reached the circuit
    opens and stops calls for ``cooldown_seconds``. After cooldown, a single
    probe is allowed (HALF_OPEN); a successful probe closes the circuit.
    """

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        send: Any = None,
    ) -> None:
        self._config = config if config is not None else CircuitBreakerConfig()
        self._send = send
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_probes = 0
        self._killed = False
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def _should_attempt_reset(self) -> bool:
        return time.monotonic() - self._opened_at >= self._config.cooldown_seconds

    def _maybe_reset(self) -> bool:
        """Internal: allow a probe when the cooldown has elapsed (test hook)."""
        with self._lock:
            if self._state is CircuitState.OPEN and self._should_attempt_reset():
                self._state = CircuitState.HALF_OPEN
                self._half_open_probes = 0
                return True
            return False

    def _on_success(self) -> None:
        with self._lock:
            if self._killed:
                return  # ponytail: kill latch blocks reset
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._half_open_probes = 0

    def _on_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def trip_kill(self) -> None:
        """Force OPEN and latch — _on_success will NOT un-trip until release_kill()."""
        import time as _time
        with self._lock:
            self._killed = True
            self._state = CircuitState.OPEN
            self._opened_at = _time.monotonic()
            self._failures = self._config.failure_threshold

    def release_kill(self) -> None:
        """Remove the kill latch, allowing normal breaker recovery."""
        with self._lock:
            self._killed = False


CorrelationId = _CorrelationId


# ----------------------------------------------------------------- TOTP cooldown

class TotpRateLimitError(RuntimeError):
    """Raised when TOTP generation is blocked by local or broker cooldown."""

    def __init__(self, message: str, *, remaining_seconds: float = 0.0) -> None:
        super().__init__(message)
        self.remaining_seconds = remaining_seconds


# Cross-platform advisory file locking (fcntl on POSIX, msvcrt on Windows)
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX uses fcntl above
    msvcrt = None  # type: ignore[assignment]


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a same-directory file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temp.open("w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        if fcntl is not None:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def _exclusive_file_lock(path: Path):
    """Context manager: advisory cross-process lock on a state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows only
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows only
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


_process_locks_guard = threading.Lock()
_process_locks: dict[str, threading.RLock] = {}


def _process_lock(path_str: str) -> threading.RLock:
    """Return the in-process single-flight lock for one state file path."""
    with _process_locks_guard:
        return _process_locks.setdefault(path_str, threading.RLock())


class TotpCooldownGuard:
    """Cross-process TOTP cooldown guard (Dhan: 120s lockout).

    Uses file locking (fcntl/msvcrt) for cross-process safety and atomic
    writes for durability across crashes. ``record_success`` marks a valid
    mint; ``acquire_attempt`` atomically checks + arms a failed attempt.
    """

    _instances: dict[str, "TotpCooldownGuard"] = {}
    _class_lock = threading.Lock()

    def __init__(
        self,
        broker: str,
        cooldown_seconds: float | None = None,
        state_path: Path | str | None = None,
    ) -> None:
        self._broker = broker.lower()
        self._cooldown_seconds = cooldown_seconds or 120.0
        self._state_path = Path(state_path) if state_path else Path(f"{broker.lower()}-totp-cooldown.json")
        self._lock_path = self._state_path.with_name(self._state_path.name + ".lock")
        self._last_attempt_at: float | None = None
        self._last_success_at: float | None = None
        self._load_state()

    def _load_state(self) -> None:
        self._last_attempt_at = None
        self._last_success_at = None
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
            self._last_attempt_at = self._coerce_wall_clock(data.get("last_attempt_at"))
            self._last_success_at = self._coerce_wall_clock(data.get("last_success_at"))
            if self._last_attempt_at is None:
                self._last_attempt_at = self._coerce_wall_clock(data.get("rate_limited_at"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return

    def _persist_state(self) -> None:
        payload = json.dumps(
            {
                "broker": self._broker,
                "last_attempt_at": self._last_attempt_at,
                "last_success_at": self._last_success_at,
            },
            indent=2,
        )
        _atomic_write_text(self._state_path, payload)

    @staticmethod
    def _coerce_wall_clock(value: object) -> float | None:
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts < 1_000_000_000:
            return None
        return ts

    def _remaining_unlocked(self) -> float:
        if self._last_attempt_at is None:
            return 0.0
        elapsed = time.time() - self._last_attempt_at
        return max(0.0, self._cooldown_seconds - elapsed)

    def check_allowed(self) -> None:
        with _process_lock(str(self._lock_path)), _exclusive_file_lock(self._lock_path):
            self._load_state()
            remaining = self._remaining_unlocked()
            if remaining > 0:
                raise TotpRateLimitError(
                    f"{self._broker} TOTP cooldown active; retry in {remaining:.0f}s",
                    remaining_seconds=remaining,
                )

    def acquire_attempt(self) -> float:
        """Atomically verify cooldown and reserve the next attempt."""
        with _process_lock(str(self._lock_path)), _exclusive_file_lock(self._lock_path):
            self._load_state()
            remaining = self._remaining_unlocked()
            if remaining > 0:
                raise TotpRateLimitError(
                    f"{self._broker} TOTP cooldown active; retry in {remaining:.0f}s",
                    remaining_seconds=remaining,
                )
            reserved_at = time.time()
            self._last_attempt_at = reserved_at
            self._persist_state()
            return reserved_at

    def record_success(self) -> None:
        with _process_lock(str(self._lock_path)), _exclusive_file_lock(self._lock_path):
            self._load_state()
            now = time.time()
            self._last_attempt_at = now
            self._last_success_at = now
            self._persist_state()


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "CorrelationId",
    "IdempotencyDuplicate",
    "MemoryIdempotencyGuard",
    "TotpCooldownGuard",
    "TotpRateLimitError",
]
