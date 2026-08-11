"""EventBus — a tiny synchronous publish/subscribe bus.

No subsystem talks directly to another; everything publishes to the bus and
listens on it. Subscribing to a base event type (e.g. ``Event``) receives all
subclasses; handler exceptions are swallowed so one bad handler can never take
down the kernel. Publishes are serialized (reentrant lock held across dispatch):
live mode has multiple producer threads (the dhanhq websocket callback vs the
LiveRunner loop), and the shared read-models they touch must never observe a
torn mid-dispatch state.
"""

from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock
from typing import Callable

from ntrade.events.base import Event

import logging
_logger = logging.getLogger("ntrade.bus")


class EventBus:
    def __init__(self, *, max_history: int = 10_000, max_handler_errors: int | None = None) -> None:
        self._subscribers: dict[type, list[Callable[[Event], None]]] = defaultdict(list)
        self._history: deque[Event] = deque(maxlen=max_history)
        self._lock = RLock()
        self._stack: list[Event] = []       # causal dispatch stack
        self._handler_errors = 0
        self._max_handler_errors = max_handler_errors

    @property
    def handler_error_count(self) -> int:
        return self._handler_errors

    def subscribe(
        self, event_type: type, handler: Callable[[Event], None]
    ) -> Callable[[Event], None]:
        """Register a handler for an event type (and its subclasses via MRO)."""
        if not isinstance(event_type, type):
            raise TypeError(f"event_type must be a class, got {event_type!r}")
        with self._lock:
            self._subscribers[event_type].append(handler)
        return handler

    def unsubscribe(self, event_type: type, handler: Callable[[Event], None]) -> None:
        with self._lock:
            try:
                self._subscribers[event_type].remove(handler)
            except ValueError:
                pass

    def publish(self, event: Event) -> None:
        """Dispatch an event to matching handlers, most-derived first.

        Handlers registered on a base class receive subclass events (dispatch
        walks the MRO). Handler exceptions are swallowed — one bad subscriber
        never kills the bus. Every event is recorded in history for replay.

        Traceability: stamps correlation_id/causation_id from the dispatch stack.
        """
        with self._lock:
            parent = self._stack[-1] if self._stack else None
            if event.correlation_id is None:
                object.__setattr__(event, "correlation_id",
                                   parent.correlation_id if parent else event.event_id)
            if event.causation_id is None and parent is not None:
                object.__setattr__(event, "causation_id", parent.event_id)
            self._stack.append(event)
            try:
                self._history.append(event)
                for klass in type(event).__mro__:
                    for handler in list(self._subscribers.get(klass, ())):
                        try:
                            handler(event)
                        except Exception:
                            self._handler_errors += 1
                            _logger.error(
                                "handler %s raised on %s correlation_id=%s (error #%d)",
                                handler, type(event).__name__,
                                getattr(event, 'correlation_id', None),
                                self._handler_errors,
                                exc_info=True,
                            )
                            if (
                                self._max_handler_errors is not None
                                and self._handler_errors >= self._max_handler_errors
                            ):
                                from ntrade.events.risk import RiskHaltedEvent
                                self.publish(RiskHaltedEvent(
                                    ts=event.ts,
                                    reason=f"handler error limit reached ({self._handler_errors} errors)",
                                ))
                                self._max_handler_errors = None  # ponytail: halt once, don't re-halt
                            continue
            finally:
                self._stack.pop()

    @property
    def history(self) -> list[Event]:
        with self._lock:
            return list(self._history)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
            self._history.clear()
            self._stack.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._history)
