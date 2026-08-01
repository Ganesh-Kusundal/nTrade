"""Trading session and market-state (State Pattern) for instruments."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MarketState(str, Enum):
    PRE_OPEN = "pre_open"
    AUCTION = "auction"
    OPEN = "open"
    HALTED = "halted"
    CIRCUIT = "circuit"
    CLOSED = "closed"
    DISCONNECTED = "disconnected"
    LIVE = "live"
    REPLAY = "replay"


@dataclass
class SessionState:
    """State owned by an instrument describing the current market session."""

    exchange: str = "NSE"
    state: MarketState = MarketState.CLOSED
    last_state_change: datetime | None = None

    def enter(self, state: MarketState, *, now: datetime | None = None) -> "SessionState":
        self.state = state
        self.last_state_change = now if now is not None else datetime.now()
        return self

    @property
    def is_open(self) -> bool:
        return self.state in (MarketState.OPEN, MarketState.LIVE, MarketState.AUCTION)

    def __repr__(self) -> str:
        return f"SessionState(exchange={self.exchange!r}, state={self.state.value!r})"


# NOTE: The old ``TradingSession = SessionState`` alias has been removed.
# ``TradingSession`` now refers to the unified SDK entry point in
# ``ntrade.kernel.trading_session``.
