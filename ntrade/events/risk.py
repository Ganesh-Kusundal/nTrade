"""Signal + risk events — the strategy→risk→OMS hand-off."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ntrade.events.base import Event


@dataclass(frozen=True, kw_only=True)
class SignalGeneratedEvent(Event):
    """A strategy decided to act. Carries the intended trade parameters."""

    symbol: str
    exchange: str
    side: str            # BUY | SELL
    quantity: int
    price: float = 0.0
    strategy: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SignalApprovedEvent(Event):
    """Risk passed the signal; OMS may materialise it."""

    signal: SignalGeneratedEvent


@dataclass(frozen=True, kw_only=True)
class SignalRejectedEvent(Event):
    """Risk blocked the signal."""

    signal: SignalGeneratedEvent
    reason: str


@dataclass(frozen=True, kw_only=True)
class RiskHaltedEvent(Event):
    """Risk circuit breaker tripped; trading must stop."""

    reason: str
    equity: float = 0.0


@dataclass(frozen=True, kw_only=True)
class RiskResumedEvent(Event):
    """Risk circuit breaker cleared; trading may resume."""

    reason: str = ""
