"""Quote and Tick — immutable market value objects owned by Instruments."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ntrade.domain.constants import QUOTE_MAX_AGE_S
from ntrade.domain.market_hours import IST as _IST
from ntrade.domain.types import assert_naive_ist


@dataclass(frozen=True)
class Quote:
    """A point-in-time market quote. Immutable by design (favor immutability)."""

    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: int = 0
    ask_qty: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    prev_close: float = 0.0
    volume: int = 0
    oi: int = 0
    vwap: float = 0.0
    avg_price: float = 0.0
    circuit_low: float = 0.0
    circuit_high: float = 0.0
    timestamp: datetime | None = None

    @classmethod
    def empty(cls) -> "Quote":
        return cls()

    # ---- derived behavior ----
    def spread(self) -> float:
        if self.bid and self.ask:
            return round(self.ask - self.bid, 4)
        return 0.0

    def mid_price(self) -> float:
        if self.bid and self.ask:
            return round((self.bid + self.ask) / 2, 4)
        return self.ltp

    def change(self) -> float:
        if self.prev_close:
            return round(self.ltp - self.prev_close, 4)
        return 0.0

    def change_pct(self) -> float:
        if self.prev_close:
            return round((self.ltp - self.prev_close) / self.prev_close * 100, 4)
        return 0.0

    def with_update(self, **kwargs) -> "Quote":
        """Return a new Quote with only the given fields changed (immutability)."""
        return replace(self, **kwargs)

    def is_stale(self, max_age_seconds: float = QUOTE_MAX_AGE_S, *, now: datetime | None = None) -> bool:
        if self.timestamp is None:
            return True
        reference = now if now is not None else datetime.now(tz=_IST)
        ts = self.timestamp
        if ts.tzinfo is None:
            # Quote.timestamp is IST wall time when naive (Quote contract) —
            # pin to IST before arithmetic so the delta is correct.
            assert_naive_ist(ts)
            ts = ts.replace(tzinfo=_IST)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=_IST)
        return (reference.astimezone(_IST) - ts.astimezone(_IST)).total_seconds() > max_age_seconds

    def as_dict(self) -> dict:
        return {
            "ltp": self.ltp, "bid": self.bid, "ask": self.ask,
            "high": self.high, "low": self.low, "open": self.open,
            "prev_close": self.prev_close, "volume": self.volume, "oi": self.oi,
            "vwap": self.vwap, "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass(frozen=True)
class Tick:
    """A single live trade/quote tick delivered to an instrument's stream."""

    symbol: str
    price: float
    quantity: int = 0
    side: str = ""  # "buy" | "sell" | ""
    timestamp: datetime | None = None
    kind: str = "trade"  # trade | quote | depth

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "price": self.price, "quantity": self.quantity,
            "side": self.side, "kind": self.kind,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
