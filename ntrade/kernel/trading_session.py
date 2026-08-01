"""TradingSession — the unified entry point for the TradeXV2 SDK.

Combines broker connection, instrument creation, the engine kernel and
strategy management into a single cohesive API::

    session = TradingSession.connect("dhan")
    nifty = session.index("NIFTY")
    nifty.market.refresh()

    session = TradingSession.paper()
    tcs = session.stock("TCS")
    session.register(tcs)
    session.register_strategy(EmaCrossStrategy())
    session.start()
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from ntrade.factories import InstrumentFactory
from ntrade.kernel.runner import StrategyRunner
from ntrade.kernel.session import TradingKernel
from ntrade.registry import BrokerRegistry

if TYPE_CHECKING:
    from ntrade.brokers.base import BrokerAdapter
    from ntrade.domain.instruments.base import Instrument
    from ntrade.domain.instruments.cash import (
        Commodity, Currency, Equity, ETF, Index,
    )
    from ntrade.domain.instruments.derivatives import Future, Option
    from ntrade.domain.portfolio import Account, Portfolio
    from ntrade.domain.scanner import ScannerFacade
    from ntrade.engines.strategy_engine import Strategy


class TradingSession:
    """Unified session: broker + kernel + strategy runner.

    Prefer the class-method constructors (``connect``, ``paper``, ``replay``)
    over direct instantiation.
    """

    def __init__(
        self,
        *,
        broker: "BrokerAdapter | None" = None,
        kernel: TradingKernel | None = None,
        mode: str = "live",
        session_id: str = "",
        initial_cash: float = 100_000.0,
        timeframe: str = "1m",
        **kernel_kw: Any,
    ):
        self._broker = broker
        self._mode = mode
        self._kernel = kernel or TradingKernel(
            mode=mode,
            broker=broker,
            session_id=session_id,
            initial_cash=initial_cash,
            timeframe=timeframe,
            **kernel_kw,
        )
        self._factory = InstrumentFactory(broker)
        self._runner = StrategyRunner(self._kernel)
        self._scanner: "ScannerFacade | None" = None

    # ============================================================ constructors

    @classmethod
    def connect(
        cls,
        broker: str,
        *,
        env_path: str = ".env",
        env: dict | None = None,
        session_id: str = "",
        initial_cash: float = 100_000.0,
        timeframe: str = "1m",
        **kw: Any,
    ) -> "TradingSession":
        """Create a live session with a named broker adapter."""
        b = BrokerRegistry.get(broker, env_path=env_path, env=env)
        return cls(
            broker=b,
            mode="live",
            session_id=session_id or f"{broker}-live",
            initial_cash=initial_cash,
            timeframe=timeframe,
            **kw,
        )

    @classmethod
    def paper(
        cls,
        *,
        session_id: str = "paper",
        initial_cash: float = 100_000.0,
        timeframe: str = "1m",
        **kw: Any,
    ) -> "TradingSession":
        """Create a paper-trading session (deterministic, offline)."""
        from ntrade.brokers.paper import PaperBroker
        b = PaperBroker()
        b._balance = initial_cash  # seed the paper broker with requested cash
        return cls(
            broker=b,
            mode="paper",
            session_id=session_id,
            initial_cash=initial_cash,
            timeframe=timeframe,
            **kw,
        )

    @classmethod
    def replay(
        cls,
        events: list,
        *,
        broker: "BrokerAdapter | None" = None,
        session_id: str = "replay",
        initial_cash: float = 100_000.0,
        timeframe: str = "1m",
        **kw: Any,
    ) -> "TradingSession":
        """Create a replay session that replays *events* through the kernel."""
        from ntrade.kernel.clock import ReplayClock
        clock = ReplayClock()
        sess = cls(
            broker=broker,
            mode="replay",
            session_id=session_id,
            initial_cash=initial_cash,
            timeframe=timeframe,
            clock=clock,
            **kw,
        )
        sess._replay_events = events
        return sess

    # ============================================================ instruments

    def stock(self, symbol: str, exchange: str | None = None, **kw) -> "Equity":
        return self._factory.equity(symbol, exchange, **kw)

    def index(self, symbol: str, exchange: str | None = None, **kw) -> "Index":
        return self._factory.index(symbol, exchange, **kw)

    def etf(self, symbol: str, exchange: str | None = None, **kw) -> "ETF":
        return self._factory.etf(symbol, exchange, **kw)

    def commodity(self, symbol: str, exchange: str | None = None, **kw) -> "Commodity":
        return self._factory.commodity(symbol, exchange, **kw)

    def currency(self, symbol: str, exchange: str | None = None, **kw) -> "Currency":
        return self._factory.currency(symbol, exchange, **kw)

    def future(self, underlying: "Instrument", expiry: date, **kw) -> "Future":
        return self._factory.future(underlying, expiry, **kw)

    def option(
        self,
        underlying: "Instrument",
        strike: float,
        expiry: date,
        option_type: str,
        **kw,
    ) -> "Option":
        return self._factory.option(underlying, strike, expiry, option_type, **kw)

    # ============================================================ account

    def account(self) -> "Account":
        from ntrade.domain.portfolio import Account
        if self._broker is not None:
            return Account.from_broker(self._broker)
        return self._kernel.ctx.account

    def portfolio(self) -> "Portfolio":
        from ntrade.domain.portfolio import Portfolio
        if self._broker is not None:
            return Portfolio.from_broker(self._broker)
        return self._kernel.ctx.portfolio

    def balance(self) -> float:
        if self._broker is not None:
            return self._broker.get_balance()
        return self._kernel.ctx.account.balance

    def positions(self):
        if self._broker is not None:
            return self._broker.get_positions()
        return self._kernel.ctx.portfolio.positions

    # ============================================================ engine stack

    def register(self, instrument: "Instrument") -> "TradingSession":
        """Register an instrument with the kernel."""
        self._kernel.register(instrument)
        return self

    def register_strategy(
        self,
        strategy: "Strategy",
        *,
        name: str | None = None,
        risk: dict | None = None,
    ) -> str:
        """Register a strategy via the StrategyRunner; returns its unique name."""
        return self._runner.add(strategy, name=name, risk=risk)

    def start(self) -> "TradingSession":
        """Start the kernel (and replay events if in replay mode)."""
        self._kernel.start()
        if self._mode == "replay" and hasattr(self, "_replay_events"):
            self._kernel.run_replay(self._replay_events)
        return self

    def stop(self, reason: str = "") -> "TradingSession":
        self._kernel.stop(reason=reason)
        return self

    # ============================================================ scanner

    def scanner(self) -> "ScannerFacade":
        """Return the scanner facade (lazily created)."""
        if self._scanner is None:
            from ntrade.domain.scanner import ScannerFacade
            self._scanner = ScannerFacade(self)
        return self._scanner

    # ============================================================ accessors

    @property
    def kernel(self) -> TradingKernel:
        """The underlying TradingKernel (escape hatch for advanced usage)."""
        return self._kernel

    @property
    def broker(self) -> "BrokerAdapter | None":
        return self._broker

    @property
    def runner(self) -> StrategyRunner:
        return self._runner

    @property
    def mode(self) -> str:
        return self._mode

    # ============================================================ lifecycle

    def connect_broker(self) -> "TradingSession":
        """Explicitly connect the broker (call ``connect()`` on the adapter)."""
        if self._broker is not None:
            self._broker.connect()
        return self

    def disconnect(self) -> None:
        if self._broker is not None:
            self._broker.disconnect()

    @property
    def connected(self) -> bool:
        return self._broker is not None and self._broker.connected

    # ============================================================ repr

    def __repr__(self) -> str:
        broker_name = self._broker.name if self._broker else "none"
        count = len(self._kernel.ctx.instruments_snapshot())
        return f"TradingSession(mode={self._mode!r}, broker={broker_name!r}, instruments={count})"
