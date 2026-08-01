"""TradingContext — the shared mutable state every engine reads and writes."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ntrade.domain.portfolio import Account, Portfolio

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument
    from ntrade.kernel.clock import TradingClock
    from ntrade.kernel.event_bus import EventBus


class TradingContext:
    """Holds the bus, clock, instruments, portfolio and account for a session."""

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

    def now(self) -> datetime:
        return self.clock.now()

    def register(self, instrument: "Instrument") -> "Instrument":
        """Register an instrument so engines can project state into it."""
        self.instruments[instrument.symbol] = instrument
        return instrument

    def instrument(self, symbol: str) -> "Instrument | None":
        return self.instruments.get(symbol)
