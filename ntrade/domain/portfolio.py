"""Portfolio & Account — composite domain objects (mission: Portfolio contains
Positions, Account contains Holdings).

Reads naturally:
    account = m.account()            # Market facade snapshot
    account.balance, account.holdings, account.positions
    account.positions[0].symbol
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from ntrade.domain.ports import BrokerAdapter


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
    ltp: float = 0.0
    product: str = "MIS"
    exchange: str = "NSE"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def market_value(self) -> float:
        return round(self.quantity * self.ltp, 2)

    @property
    def pnl(self) -> float:
        return round((self.ltp - self.avg_price) * self.quantity, 2)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "quantity": self.quantity,
            "avg_price": self.avg_price, "ltp": self.ltp,
            "market_value": self.market_value, "pnl": self.pnl,
            "product": self.product, "exchange": self.exchange,
        }


@dataclass
class Holding:
    symbol: str
    quantity: int
    avg_price: float
    ltp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        return round((self.ltp - self.avg_price) * self.quantity, 2)

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "quantity": self.quantity,
                "avg_price": self.avg_price, "ltp": self.ltp, "pnl": self.pnl}


class Portfolio:
    """Composite of Positions (and optionally Holdings)."""

    def __init__(self, positions: list[Position] | None = None,
                 holdings: list[Holding] | None = None, broker: "BrokerAdapter | None" = None):
        self.positions: list[Position] = positions or []
        self.holdings: list[Holding] = holdings or []
        self._broker = broker

    @classmethod
    def from_broker(cls, broker: "BrokerAdapter") -> "Portfolio":
        """Build a Portfolio snapshot from a broker adapter (adapter normalizes)."""
        positions = broker.get_positions()
        holdings = broker.get_holdings()
        return cls(positions=positions or [], holdings=holdings or [], broker=broker)

    @property
    def symbol(self) -> str:
        return "portfolio"

    @property
    def pnl(self) -> float:
        return round(sum(p.pnl for p in self.positions), 2)

    @property
    def live_pnl(self) -> float:
        """Broker-reported live P&L (realised + unrealised), 0 when unknown."""
        if self._broker is not None:
            try:
                return round(float(self._broker.get_live_pnl() or 0.0), 2)
            except Exception:
                return 0.0
        return self.pnl

    @property
    def market_value(self) -> float:
        return round(sum(p.market_value for p in self.positions), 2)

    def position(self, symbol: str) -> Position | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def refresh(self) -> "Portfolio":
        if self._broker is not None:
            refreshed = Portfolio.from_broker(self._broker)
            self.positions = refreshed.positions
            self.holdings = refreshed.holdings
        return self

    def __len__(self) -> int:
        return len(self.positions)

    def __iter__(self) -> Iterator[Position]:
        return iter(self.positions)

    def __getitem__(self, symbol: str) -> Position:
        pos = self.position(symbol)
        if pos is None:
            raise KeyError(f"No position for {symbol!r}")
        return pos

    def as_dict(self) -> dict:
        return {
            "positions": [p.as_dict() for p in self.positions],
            "holdings": [h.as_dict() for h in self.holdings],
            "pnl": self.pnl, "market_value": self.market_value,
        }

    def __repr__(self) -> str:
        return f"Portfolio(positions={len(self.positions)}, pnl={self.pnl})"


class Account:
    """Account contains Holdings + balance (Composite over the broker)."""

    def __init__(self, balance: float = 0.0, holdings: list[Holding] | None = None,
                 broker: "BrokerAdapter | None" = None, **metadata: Any):
        self.balance = balance
        self.holdings: list[Holding] = holdings or []
        self._broker = broker
        self.metadata = metadata

    @classmethod
    def from_broker(cls, broker: "BrokerAdapter") -> "Account":
        return cls(balance=broker.get_balance(),
                   holdings=broker.get_holdings() or [], broker=broker)

    def holding(self, symbol: str) -> Holding | None:
        for h in self.holdings:
            if h.symbol == symbol:
                return h
        return None

    def refresh(self) -> "Account":
        if self._broker is not None:
            fresh = Account.from_broker(self._broker)
            self.balance = fresh.balance
            self.holdings = fresh.holdings
        return self

    def as_dict(self) -> dict:
        return {"balance": self.balance, "holdings": [h.as_dict() for h in self.holdings]}

    def __len__(self) -> int:
        return len(self.holdings)

    def __repr__(self) -> str:
        return f"Account(balance={self.balance}, holdings={len(self.holdings)})"
