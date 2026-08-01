"""EventStore — append-only, replayable event record.

Every event the kernel sees can be appended here for deterministic replay,
debugging, auditing and crash recovery. Persists to JSONL when a path is given;
otherwise stays in memory.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

from ntrade.events.base import Event
from ntrade.events import lifecycle, market, order, portfolio, risk

_EVENT_TYPES: dict[str, type] = {}


def _register(module):
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, Event) and obj is not Event:
            _EVENT_TYPES[obj.__name__] = obj


for _mod in (market, order, portfolio, risk, lifecycle):
    _register(_mod)


def _to_jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _from_jsonable(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _encode(event: Event) -> dict:
    """Serialize an event to a JSON-safe dict (recurses into nested events)."""
    out = {"__type__": type(event).__name__}
    for field in dataclasses.fields(event):
        value = getattr(event, field.name)
        out[field.name] = _encode(value) if isinstance(value, Event) else _to_jsonable(value)
    return out


def _decode(data: dict) -> Event | None:
    """Reconstruct an event (recurses into nested events via __type__)."""
    cls = _EVENT_TYPES.get(data.get("__type__"))
    if cls is None:
        return None  # unknown event type — skip gracefully
    kwargs = {}
    for key, value in data.items():
        if key == "__type__":
            continue
        if isinstance(value, dict) and "__type__" in value:
            kwargs[key] = _decode(value)
        else:
            kwargs[key] = _from_jsonable(value)
    return cls(**kwargs)


class EventStore:
    def __init__(self, path: str | None = None):
        self.path = Path(path) if path else None
        self._events: list[Event] = []
        self._fh = None
        if self.path is not None and self.path.exists():
            self._load()

    def _ensure_fh(self):
        if self._fh is None and self.path is not None:
            self._fh = open(self.path, "a", encoding="utf-8")
        return self._fh

    def append(self, event: Event) -> "EventStore":
        self._events.append(event)
        fh = self._ensure_fh()
        if fh is not None:
            fh.write(json.dumps(_encode(event)) + "\n")
            fh.flush()
        return self

    def extend(self, events) -> "EventStore":
        for event in events:
            self.append(event)
        return self

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    event = _decode(json.loads(line))
                    if event is not None:
                        self._events.append(event)

    # ------------------------------------------------------------------ query
    def events(self, event_type=None, symbol: str | None = None) -> list[Event]:
        out = []
        for event in self._events:
            if event_type is not None and not isinstance(event, event_type):
                continue
            if symbol is not None and getattr(event, "symbol", None) != symbol:
                continue
            out.append(event)
        return out

    def market_events(self):
        """Only the causal market-data events (Tick/Quote/Depth).

        Replay these — derived events (signals/fills) are recomputed by the
        kernel and must never be re-fed, or they would double-apply.
        """
        from ntrade.events.market import DepthEvent, QuoteEvent, TickEvent

        types = (TickEvent, QuoteEvent, DepthEvent)
        return [e for e in self._events if isinstance(e, types)]

    def recovery_events(self):
        """Causal events for crash recovery: market data + fills.

        A crash-recovery kernel replays exactly these to rebuild instrument,
        candle, indicator and portfolio state deterministically. Derived
        events (signals, intents, candle/indicator/balance updates) are
        recomputed by the kernel and must never be re-fed.

        Recorded order is *effect-then-cause*: the kernel's record handler runs
        last on the base ``Event`` subscription, so each tick is appended after
        its nested effects (the fill it triggered). We re-sort so a fill always
        replays *after* the market event that caused it (market first on a
        timestamp tie) — otherwise a fill would run against stale instrument
        state and diverge from the crashed session.
        """
        from ntrade.events.market import DepthEvent, QuoteEvent, TickEvent
        from ntrade.events.order import OrderFilledEvent

        market_types = (TickEvent, QuoteEvent, DepthEvent)
        types = market_types + (OrderFilledEvent,)
        events = [e for e in self._events if isinstance(e, types)]
        return sorted(
            events, key=lambda e: (e.ts, 0 if isinstance(e, market_types) else 1)
        )

    def replay(self):
        """Iterate recorded events in chronological order."""
        return iter(sorted(self._events, key=lambda e: e.ts))

    def clear(self) -> "EventStore":
        self._events.clear()
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        if self.path is not None:
            self.path.unlink(missing_ok=True)
        return self

    def close(self) -> "EventStore":
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        return self

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)
