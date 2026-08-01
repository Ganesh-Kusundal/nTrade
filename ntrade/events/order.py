"""Order lifecycle events — from SignalApproved through execution to fill."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ntrade.events.base import Event


@dataclass(frozen=True, kw_only=True)
class OrderIntentEvent(Event):
    """An approved signal materialised as an order intent (pre-execution)."""

    symbol: str
    exchange: str
    side: str            # BUY | SELL
    quantity: int
    order_type: str = "LIMIT"
    price: float = 0.0
    strategy: str = ""


@dataclass(frozen=True, kw_only=True)
class OrderAcceptedEvent(Event):
    """The execution target accepted the order."""

    order_id: str
    symbol: str
    exchange: str
    side: str
    quantity: int
    strategy: str = ""


@dataclass(frozen=True, kw_only=True)
class OrderRejectedEvent(Event):
    """The execution target (or risk) rejected the order."""

    order_id: str = ""
    symbol: str
    exchange: str
    side: str
    quantity: int
    reason: str
    strategy: str = ""


@dataclass(frozen=True, kw_only=True)
class OrderFilledEvent(Event):
    """An order (fully) filled at a price."""

    order_id: str
    symbol: str
    exchange: str
    side: str
    quantity: int
    fill_price: float
    commission: float = 0.0
    strategy: str = ""


@dataclass(frozen=True, kw_only=True)
class OrderUpdatedEvent(Event):
    """An open order's lifecycle status changed (PENDING/PARTIALLY_FILLED/...)."""

    order_id: str
    symbol: str
    exchange: str
    side: str
    status: str
    filled_qty: int = 0
    avg_price: float = 0.0
    strategy: str = ""


@dataclass(frozen=True, kw_only=True)
class OrderTimeoutEvent(Event):
    """A PENDING order exceeded its timeout threshold without being filled."""

    order_id: str
    symbol: str
    exchange: str
    side: str
    quantity: int
    age_seconds: float
    strategy: str = ""
