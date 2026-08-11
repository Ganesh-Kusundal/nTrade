"""Portfolio events — positions and balances pushed by the PortfolioEngine."""

from __future__ import annotations

from dataclasses import dataclass

from ntrade.events.base import Event


@dataclass(frozen=True, kw_only=True)
class PositionUpdatedEvent(Event):
    """A position's quantity/avg-price changed (net after a fill)."""

    symbol: str
    exchange: str
    quantity: int
    avg_price: float = 0.0
    ltp: float = 0.0


@dataclass(frozen=True, kw_only=True)
class BalanceChangedEvent(Event):
    """Account balance changed (e.g. after a fill or cash move)."""

    balance: float
