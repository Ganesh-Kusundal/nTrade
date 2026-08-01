"""OrderBook and TradeBook — immutable domain types for broker book data.

Brokers return order and trade books as raw dicts or DataFrames. These types
normalize that data into frozen, typed value objects that never leak pandas
or dict structures across the broker boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class OrderBookEntry:
    """A single open order in the order book."""

    symbol: str = ""
    order_id: str = ""
    side: str = ""            # "BUY" | "SELL"
    quantity: int = 0
    price: float = 0.0
    status: str = ""          # OrderStatus value string
    exchange: str = ""
    timestamp: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "order_id": self.order_id,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "status": self.status,
            "exchange": self.exchange,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass(frozen=True)
class OrderBook:
    """Immutable collection of open orders.

    Created by the broker adapter, consumed by the caller. Value object —
    frozen dataclass with tuple entries for thread-safe reads.
    """

    entries: tuple[OrderBookEntry, ...] = ()
    timestamp: datetime | None = None

    def for_symbol(self, symbol: str) -> list[OrderBookEntry]:
        """Filter entries by symbol."""
        return [e for e in self.entries if e.symbol == symbol]

    def to_dicts(self) -> list[dict]:
        """Escape hatch: convert to list of row dicts (backward compat)."""
        return [e.to_dict() for e in self.entries]

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __getitem__(self, index: int) -> OrderBookEntry:
        return self.entries[index]


@dataclass(frozen=True)
class TradeBookEntry:
    """A single executed trade in the trade book."""

    symbol: str = ""
    trade_id: str = ""
    order_id: str = ""
    side: str = ""
    quantity: int = 0
    price: float = 0.0
    timestamp: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass(frozen=True)
class TradeBook:
    """Immutable collection of executed trades.

    Created by the broker adapter, consumed by the caller. Value object —
    frozen dataclass with tuple entries for thread-safe reads.
    """

    entries: tuple[TradeBookEntry, ...] = ()
    timestamp: datetime | None = None

    def for_symbol(self, symbol: str) -> list[TradeBookEntry]:
        """Filter entries by symbol."""
        return [e for e in self.entries if e.symbol == symbol]

    def to_dicts(self) -> list[dict]:
        """Escape hatch: convert to list of row dicts (backward compat)."""
        return [e.to_dict() for e in self.entries]

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __getitem__(self, index: int) -> TradeBookEntry:
        return self.entries[index]
