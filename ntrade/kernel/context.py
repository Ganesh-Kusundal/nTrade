"""TradingContext — the shared mutable state every engine reads and writes."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ntrade.domain.portfolio import Account, Portfolio

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument
    from ntrade.kernel.clock import TradingClock
    from ntrade.kernel.event_bus import EventBus


class TradingContext:
    """Holds the bus, clock, instruments, portfolio and account for a session.

    Thread safety: a reentrant lock (``ctx.lock``) protects instrument
    registration and iteration.  Engines that mutate shared state from
    different threads (WebSocket feed vs. order thread) should acquire
    ``ctx.lock`` around the critical section.
    """

    def __init__(
        self,
        bus: "EventBus",
        clock: "TradingClock",
        *,
        mode: str = "live",
        instruments: dict[str, "Instrument"] | None = None,
        portfolio: Portfolio | None = None,
        account: Account | None = None,
        session_id: str = "",
        **metadata: Any,
    ):
        self.bus = bus
        self.clock = clock
        self.mode = mode  # live | replay | backtest
        self.instruments: dict[str, "Instrument"] = instruments or {}
        self.portfolio = portfolio or Portfolio()
        self.account = account or Account()
        self.session_id = session_id
        self.metadata = metadata
        self.lock = threading.RLock()

    def now(self) -> datetime:
        return self.clock.now()

    def register(self, instrument: "Instrument") -> "Instrument":
        """Register an instrument so engines can project state into it."""
        with self.lock:
            self.instruments[instrument.symbol] = instrument
        return instrument

    def instrument(self, symbol: str) -> "Instrument | None":
        with self.lock:
            return self.instruments.get(symbol)

    def instruments_snapshot(self) -> list["Instrument"]:
        """Thread-safe list copy of all registered instruments.

        Shallow: the Instrument read-models inside are still mutated by the
        feed thread (``apply_quote``) — use :meth:`instruments_deep_snapshot`
        when serializing/iterating state that must be internally consistent.
        """
        with self.lock:
            return list(self.instruments.values())

    def instruments_deep_snapshot(self) -> dict[str, dict]:
        """Thread-safe deep snapshot: symbol -> ``instrument.snapshot()``.

        Each entry is captured under the context lock, so concurrent feed
        updates cannot tear a single instrument's snapshot.
        """
        with self.lock:
            return {s: i.snapshot() for s, i in self.instruments.items()}
