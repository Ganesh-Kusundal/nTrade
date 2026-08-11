"""Kernel lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass

from ntrade.events.base import Event


@dataclass(frozen=True, kw_only=True)
class KernelStartedEvent(Event):
    """The TradingKernel finished wiring and is live."""

    mode: str = "live"   # live | replay | backtest


@dataclass(frozen=True, kw_only=True)
class SessionStartedEvent(Event):
    """A trading session (live day / replay run / backtest) began."""

    session_id: str = ""


@dataclass(frozen=True, kw_only=True)
class SessionStoppedEvent(Event):
    """A trading session ended; engines flush and close."""

    session_id: str = ""
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class RunnerStartedEvent(Event):
    """LiveRunner began executing its orchestration loop."""


@dataclass(frozen=True, kw_only=True)
class RunnerStoppedEvent(Event):
    """LiveRunner stopped and cleaned up its feed and kernel."""

    reason: str = ""

@dataclass(frozen=True, kw_only=True)
class HeartbeatEvent(Event):
    """Periodic heartbeat from the LiveRunner — proves the kernel is alive."""

    tick_count: int = 0
    open_orders: int = 0


@dataclass(frozen=True, kw_only=True)
class FeedDisconnectedEvent(Event):
    """The market feed websocket disconnected."""

    reason: str = ""
