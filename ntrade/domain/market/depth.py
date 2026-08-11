"""MarketDepth — an immutable order-book snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: int
    orders: int = 0


@dataclass(frozen=True)
class MarketDepth:
    symbol: str
    bids: tuple[DepthLevel, ...] = ()
    asks: tuple[DepthLevel, ...] = ()
    timestamp: datetime | None = None

    @classmethod
    def empty(cls, symbol: str = "") -> "MarketDepth":
        return cls(symbol=symbol)

    def best_bid(self) -> DepthLevel | None:
        return self.bids[0] if self.bids else None

    def best_ask(self) -> DepthLevel | None:
        return self.asks[0] if self.asks else None

    def spread(self) -> float:
        bb, ba = self.best_bid(), self.best_ask()
        if bb and ba:
            return round(ba.price - bb.price, 4)
        return 0.0

    def depth(self, levels: int = 5) -> tuple[list[DepthLevel], list[DepthLevel]]:
        return list(self.bids[:levels]), list(self.asks[:levels])

    def bid_ask_imbalance(self) -> float:
        """(bid qty - ask qty) / (bid qty + ask qty); positive = buy pressure."""
        bid_qty = sum(level.quantity for level in self.bids)
        ask_qty = sum(level.quantity for level in self.asks)
        if bid_qty + ask_qty == 0:
            return 0.0
        return round((bid_qty - ask_qty) / (bid_qty + ask_qty), 4)
