"""Canonical event model — the lingua franca of the trading kernel.

Every subsystem communicates by publishing/consuming these events. Events are
frozen dataclasses (immutable, hashable) so they can be recorded, replayed and
compared deterministically. `ts` always comes from a TradingClock.
"""

from ntrade.events.base import Event
from ntrade.events.market import (
    CandleClosedEvent,
    DepthEvent,
    IndicatorUpdatedEvent,
    QuoteEvent,
    QuoteUpdatedEvent,
    TickEvent,
    WatchlistReady,
)
from ntrade.events.order import (
    OrderAcceptedEvent,
    OrderFilledEvent,
    OrderIntentEvent,
    OrderRejectedEvent,
    OrderTimeoutEvent,
    OrderUpdatedEvent,
)
from ntrade.events.portfolio import BalanceChangedEvent, PositionUpdatedEvent
from ntrade.events.risk import (
    RiskHaltedEvent,
    RiskResumedEvent,
    SignalApprovedEvent,
    SignalGeneratedEvent,
    SignalRejectedEvent,
)
from ntrade.events.lifecycle import (
    FeedDisconnectedEvent,
    HeartbeatEvent,
    KernelStartedEvent,
    RunnerStartedEvent,
    RunnerStoppedEvent,
    SessionStartedEvent,
    SessionStoppedEvent,
)

__all__ = [
    "Event",
    "TickEvent", "QuoteEvent", "DepthEvent", "CandleClosedEvent",
    "QuoteUpdatedEvent", "IndicatorUpdatedEvent",
    "WatchlistReady",
    "OrderIntentEvent", "OrderAcceptedEvent", "OrderRejectedEvent", "OrderFilledEvent",
    "OrderUpdatedEvent", "OrderTimeoutEvent",
    "PositionUpdatedEvent", "BalanceChangedEvent",
    "SignalGeneratedEvent", "SignalApprovedEvent", "SignalRejectedEvent",
    "RiskHaltedEvent", "RiskResumedEvent",
    "KernelStartedEvent", "SessionStartedEvent", "SessionStoppedEvent",
    "RunnerStartedEvent", "RunnerStoppedEvent", "HeartbeatEvent", "FeedDisconnectedEvent",
]
