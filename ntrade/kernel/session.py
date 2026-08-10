"""TradingKernel — coordinates every subsystem. Contains no trading logic.

Responsibilities: initialise the engine stack, register strategies and
instruments, wire event handlers, start/stop sessions, and replay event
streams. The only interchangeable components are the market event source, the
execution target and the trading clock — everything else stays identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ntrade.engines.candle_engine import CandleEngine
from ntrade.engines.indicator_engine import IndicatorEngine
from ntrade.engines.market_engine import MarketEngine
from ntrade.engines.order_engine import OrderEngine
from ntrade.engines.portfolio_engine import PortfolioEngine
from ntrade.engines.position_sync import PositionSyncEngine
from ntrade.engines.risk_engine import RiskEngine
from ntrade.engines.strategy_engine import Strategy, StrategyEngine
from ntrade.events.base import Event
from ntrade.events.lifecycle import (
    KernelStartedEvent, SessionStartedEvent, SessionStoppedEvent,
)
from ntrade.execution.broker_executor import BrokerExecution
from ntrade.execution.costs import STATUTORY_DEFAULT
from ntrade.execution.router import ExecutionRouter
from ntrade.execution.simulator import SimulatedExecution
from ntrade.kernel.clock import LiveClock, ReplayClock, TradingClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus
from ntrade.domain.constants import DEFAULT_TIMEFRAME

if TYPE_CHECKING:
    from ntrade.domain.ports import BrokerAdapter
    from ntrade.domain.instruments.base import Instrument


class TradingKernel:
    """Wires the engine stack. ``mode`` is live | replay | backtest — only the
    clock and execution target differ between modes; the stack is identical."""

    def __init__(
        self,
        *,
        mode: str = "live",
        bus: EventBus | None = None,
        clock: TradingClock | None = None,
        timeframe: str = DEFAULT_TIMEFRAME,
        instruments: dict[str, "Instrument"] | None = None,
        broker: "BrokerAdapter | None" = None,
        execution=None,
        session_id: str = "",
        initial_cash: float = 100_000.0,
        store=None,
        statutory=STATUTORY_DEFAULT,
        order_timeout_seconds: float = 300.0,
    ):
        self.mode = mode
        self.session_id = session_id
        self.bus = bus or EventBus()
        self.clock = clock or LiveClock()
        self.ctx = TradingContext(
            self.bus, self.clock, mode=mode,
            instruments=instruments or {}, session_id=session_id,
        )
        self.ctx.account.balance = initial_cash
        # Inject the kernel clock into the broker so its timestamps (quotes,
        # depth, books) follow replay time, not the wall clock (zero parity).
        if broker is not None and hasattr(broker, "set_clock"):
            broker.set_clock(self.clock)

        # --- optional full event recording (EventStore) ----------------------
        # Subscribing to the base Event class records every published event
        # (market data, signals, fills, lifecycle) for audit + deterministic
        # replay. Replay should feed store.market_events(), not derived events.
        self.store = store
        if store is not None:
            self.bus.subscribe(Event, self._record)

        # --- engine stack (mode-independent) ---------------------------------
        self.market_engine = MarketEngine(self.ctx)
        self.candle_engine = CandleEngine(self.ctx, timeframe=timeframe)
        self.indicator_engine = IndicatorEngine(self.ctx, timeframe=timeframe)
        self.strategy_engine = StrategyEngine(self.ctx)
        self.risk_engine = RiskEngine(self.ctx)
        self.portfolio_engine = PortfolioEngine(self.ctx)

        # --- execution target (the only interchangeable piece) ---------------
        self.broker = broker
        self.position_sync = PositionSyncEngine(self.ctx, broker) if broker is not None else None
        if execution is None:
            execution = ExecutionRouter(self.ctx)
            if broker is not None:
                # Live target charges the same statutory schedule as the sim
                # target (H6); pass statutory=None to opt into zero-cost live.
                execution.add("default", BrokerExecution(self.ctx, broker, statutory=statutory,
                                                              order_timeout_seconds=order_timeout_seconds))
            else:
                # Simulated target: statutory Indian charges by default (H6) so
                # paper/backtest PnL converges on live; pass statutory=None to
                # opt back into zero-cost simulation.
                execution.add("default", SimulatedExecution(self.ctx, statutory=statutory))
            execution.default("default")
        self.router = execution
        self.order_engine = OrderEngine(self.ctx, router=self.router)

    # ------------------------------------------------------------------ wiring
    def register(self, instrument: "Instrument") -> "TradingKernel":
        self.ctx.register(instrument)
        return self

    def register_strategy(self, strategy: Strategy) -> "TradingKernel":
        self.strategy_engine.register(strategy)
        return self

    def publish(self, event) -> "TradingKernel":
        self.bus.publish(event)
        return self

    def _record(self, event) -> None:
        self.store.append(event)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "TradingKernel":
        self.bus.publish(KernelStartedEvent(mode=self.mode, ts=self.clock.now()))
        self.bus.publish(SessionStartedEvent(session_id=self.session_id, ts=self.clock.now()))
        return self

    def stop(self, reason: str = "") -> "TradingKernel":
        self.candle_engine.flush()
        self.bus.publish(SessionStoppedEvent(
            session_id=self.session_id, reason=reason, ts=self.clock.now(),
        ))
        return self

    # ------------------------------------------------------------------ replay
    def run_replay(self, events, *, start=None) -> "TradingKernel":
        """Feed a timestamped event stream through the bus.

        With a ReplayClock the clock follows the events, so the same stream
        produces the same decisions as live trading (zero parity).
        """
        if start is not None and hasattr(self.clock, "set"):
            self.clock.set(start)
        for event in events:
            if hasattr(self.clock, "set"):
                self.clock.set(event.ts)
            self.bus.publish(event)
        return self

    # ------------------------------------------------------------------ live
    def broker_execution(self) -> "BrokerExecution | None":
        """The live BrokerExecution target on the router (None in sim modes)."""
        if isinstance(self.router, ExecutionRouter):
            for target in self.router.targets():
                if isinstance(target, BrokerExecution):
                    return target
        return None

    def poll_orders(self) -> list:
        """Refresh open live orders; publish fills/rejections the broker reports."""
        execution = self.broker_execution()
        if execution is None:
            return []
        return execution.poll()

    def sync_positions(self) -> int:
        """Reconcile broker-reported positions/balance into the kernel (live)."""
        if self.position_sync is None:
            return len(self.ctx.portfolio.positions)
        return self.position_sync.sync()

    # ------------------------------------------------------------------ OMS
    def open_orders(self) -> list[str]:
        """Order ids still awaiting broker lifecycle (live mode only)."""
        execution = self.broker_execution()
        if execution is None:
            return []
        return execution.open_orders()

    def modify_order(self, order_id: str, **kw):
        """Modify an open live order (delegates to the broker)."""
        execution = self.broker_execution()
        if execution is None:
            return None
        return execution.modify(order_id, **kw)

    def cancel_order(self, order_id: str):
        """Cancel an open live order (delegates to the broker)."""
        execution = self.broker_execution()
        if execution is None:
            return None
        return execution.cancel(order_id)

    # ------------------------------------------------------------------ helpers
    def replay_clock(self) -> ReplayClock | None:
        return self.clock if isinstance(self.clock, ReplayClock) else None

    @property
    def balance(self) -> float:
        return self.ctx.account.balance
