"""LiveStream — every instrument owns its own subscription lifecycle.

The stream manages callbacks and state; the actual websocket transport is shared
behind the broker adapter (subscriptions multiplex internally).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Callable

import pandas as pd

from ntrade.domain.market.quote import Tick

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument


class SubscriptionState:
    NOT_SUBSCRIBED = "not_subscribed"
    SUBSCRIBED = "subscribed"
    STREAMING = "streaming"


class LiveStream:
    EVENT_NAMES = ("tick", "quote", "trade", "depth", "disconnect", "reconnect")

    def __init__(self, instrument: "Instrument"):
        self.instrument = instrument
        self.state = SubscriptionState.NOT_SUBSCRIBED
        self.last_tick: Tick | None = None
        self._ticks: deque = deque(maxlen=10_000)
        self._handlers: dict[str, list[Callable]] = {e: [] for e in self.EVENT_NAMES}

    def __call__(self) -> "LiveStream":
        """Callable accessor: instrument.stream() returns the stream itself."""
        return self

    # ------------------------------------------------------------------ state
    @property
    def is_live(self) -> bool:
        return self.state in (SubscriptionState.SUBSCRIBED, SubscriptionState.STREAMING)

    @property
    def is_subscribed(self) -> bool:
        return self.state != SubscriptionState.NOT_SUBSCRIBED

    @property
    def tick_count(self) -> int:
        return len(self._ticks)

    def ticks(self, limit: int | None = None) -> list[Tick]:
        if limit is None:
            return list(self._ticks)
        return list(self._ticks[-limit:])

    @property
    def live_ticks_df(self) -> pd.DataFrame:
        if not self._ticks:
            return pd.DataFrame()
        return pd.DataFrame([t.as_dict() for t in self._ticks])

    # ------------------------------------------------------------------ lifecycle
    def subscribe(self) -> "LiveStream":
        if not self.is_subscribed:
            broker = self.instrument.broker_adapter
            if broker is not None:
                broker.subscribe(self.instrument)
            self.state = SubscriptionState.SUBSCRIBED
        return self

    def unsubscribe(self) -> "LiveStream":
        if self.is_subscribed:
            broker = self.instrument.broker_adapter
            if broker is not None:
                broker.unsubscribe(self.instrument)
            self.state = SubscriptionState.NOT_SUBSCRIBED
        return self

    # ------------------------------------------------------------------ events
    def on(self, event: str, callback: Callable) -> Callable:
        """Register a handler; usable as a decorator."""
        if event not in self._handlers:
            raise ValueError(f"Unknown event {event!r}; expected one of {self.EVENT_NAMES}")
        self._handlers[event].append(callback)
        return callback

    def on_tick(self, cb): return self.on("tick", cb)
    def on_quote(self, cb): return self.on("quote", cb)
    def on_trade(self, cb): return self.on("trade", cb)
    def on_depth(self, cb): return self.on("depth", cb)
    def on_disconnect(self, cb): return self.on("disconnect", cb)
    def on_reconnect(self, cb): return self.on("reconnect", cb)

    def _emit(self, event: str, payload) -> None:
        for cb in list(self._handlers[event]):
            try:
                cb(payload)
            except Exception:
                # Callback errors must not kill the stream.
                continue

    # ------------------------------------------------------------------ ingest
    def ingest_tick(self, tick: Tick) -> None:
        """Apply a live tick: update quote state, cache tick, emit events."""
        self.last_tick = tick
        self._ticks.append(tick)
        if tick.kind == "quote":
            self.instrument._quote = self.instrument._quote.with_update(
                ltp=tick.price, bid=tick.price, ask=tick.price, timestamp=tick.timestamp or datetime.now(),
            )
            self._emit("quote", tick)
        elif tick.kind == "trade":
            self.instrument._quote = self.instrument._quote.with_update(
                ltp=tick.price, timestamp=tick.timestamp or datetime.now(),
            )
            self._emit("trade", tick)
        else:
            self._emit("depth", tick)
        self._emit("tick", tick)

    def notify_disconnect(self) -> None:
        self.state = SubscriptionState.NOT_SUBSCRIBED
        self._emit("disconnect", None)

    def notify_reconnect(self) -> None:
        self.state = SubscriptionState.SUBSCRIBED
        self._emit("reconnect", None)
