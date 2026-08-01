"""ntrade — an institutional-grade, object-oriented trading framework.

The public API is a set of rich market domain objects (Equity, Index, Option,
OptionChain, ...). Broker transport, websockets, REST and JSON stay hidden
behind broker adapters.
"""

from ntrade.domain.instruments.cash import (
    Bond, Commodity, Crypto, Currency, Equity, ETF, Index, Spot,
)
from ntrade.domain.instruments.chain import OptionChain
from ntrade.domain.instruments.derivatives import Future, Option, SyntheticInstrument
from ntrade.domain.instruments.base import Instrument
from ntrade.domain.market.candles import CandleSeries
from ntrade.domain.market.depth import DepthLevel, MarketDepth
from ntrade.domain.market.quote import Quote, Tick
from ntrade.domain.analytics.greeks import Greeks
from ntrade.facade import Market
from ntrade.factories import InstrumentFactory
from ntrade.registry import BrokerRegistry, SymbolMaster
from ntrade.domain.orders.order import Order, OrderSide, OrderStatus, OrderType, TradeType
from ntrade.domain.session import MarketState, SessionState

# --- event-centric kernel (zero parity) ------------------------------------
from ntrade.events.base import Event
from ntrade.events.market import (
    CandleClosedEvent, DepthEvent, IndicatorUpdatedEvent, QuoteEvent,
    QuoteUpdatedEvent, TickEvent,
)
from ntrade.events.order import (
    OrderAcceptedEvent, OrderFilledEvent, OrderIntentEvent, OrderRejectedEvent,
    OrderTimeoutEvent, OrderUpdatedEvent,
)
from ntrade.events.portfolio import BalanceChangedEvent, PositionUpdatedEvent
from ntrade.events.risk import (
    RiskHaltedEvent, RiskResumedEvent,
    SignalApprovedEvent, SignalGeneratedEvent, SignalRejectedEvent,
)
from ntrade.events.lifecycle import (
    HeartbeatEvent, FeedDisconnectedEvent,
    KernelStartedEvent, RunnerStartedEvent, RunnerStoppedEvent,
    SessionStartedEvent, SessionStoppedEvent,
)
from ntrade.kernel.clock import LiveClock, ReplayClock, SimulationClock, TradingClock
from ntrade.kernel.event_bus import EventBus
from ntrade.kernel.session import TradingKernel
from ntrade.kernel.resilient import ResilientKernel
from ntrade.kernel.runner import StrategyRunner
from ntrade.kernel.trading_session import TradingSession
from ntrade.engines.strategy_engine import Strategy
from ntrade.execution.broker_executor import BrokerExecution
from ntrade.execution.retry import RetryPolicy
from ntrade.execution.costs import FixedSlippage, PercentageSlippage, FlatCommission, PercentageCommission
from ntrade.execution.router import ExecutionRouter
from ntrade.execution.simulator import SimulatedExecution
from ntrade.storage.event_store import EventStore
from ntrade.replay.replay_engine import ReplayEngine
from ntrade.backtest.simulator import BacktestResult, BacktestSimulator
from ntrade.backtest.fills import BarAwareExecution, FillPolicy
from ntrade.sources.market_feed import MarketFeedSource, SimulatedFeedSource
from ntrade.sources.dhan_feed import DhanMarketFeedSource, dhan_payload_to_events
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource
from ntrade.runner.live_runner import LiveRunner
from ntrade.sim.tick_simulator import SimTick, synthesize_1m_ticks
from ntrade.domain.scanner import Scanner, ScannerFacade, ScannerResult

__version__ = "0.2.0"

__all__ = [
    "Instrument",
    "Equity", "Index", "ETF", "Currency", "Commodity", "Bond", "Crypto", "Spot",
    "Future", "Option", "OptionChain", "SyntheticInstrument",
    "Quote", "Tick", "CandleSeries", "DepthLevel", "MarketDepth", "Greeks",
    "Order", "OrderSide", "OrderType", "OrderStatus", "TradeType",
    "MarketState", "SessionState",
    "Market", "InstrumentFactory", "BrokerRegistry", "SymbolMaster",
    # event-centric kernel
    "Event",
    "TickEvent", "QuoteEvent", "DepthEvent", "CandleClosedEvent",
    "QuoteUpdatedEvent", "IndicatorUpdatedEvent",
    "OrderIntentEvent", "OrderAcceptedEvent", "OrderRejectedEvent", "OrderFilledEvent",
    "OrderUpdatedEvent", "OrderTimeoutEvent",
    "PositionUpdatedEvent", "BalanceChangedEvent",
    "SignalGeneratedEvent", "SignalApprovedEvent", "SignalRejectedEvent",
    "RiskHaltedEvent", "RiskResumedEvent",
    "KernelStartedEvent", "SessionStartedEvent", "SessionStoppedEvent",
    "RunnerStartedEvent", "RunnerStoppedEvent",
    "HeartbeatEvent", "FeedDisconnectedEvent",
    "TradingClock", "LiveClock", "ReplayClock", "SimulationClock",
    "EventBus", "TradingKernel", "Strategy",
    "ResilientKernel", "StrategyRunner", "TradingSession",
    "ExecutionRouter", "SimulatedExecution", "BrokerExecution", "RetryPolicy",
    "FixedSlippage", "PercentageSlippage", "FlatCommission", "PercentageCommission",
    "EventStore", "ReplayEngine", "BacktestSimulator", "BacktestResult", "FillPolicy",
    "BarAwareExecution", "MarketFeedSource", "SimulatedFeedSource",
    "DhanMarketFeedSource", "dhan_payload_to_events",
    "SyntheticMarketFeedSource", "LiveRunner", "SimTick", "synthesize_1m_ticks",
    "Scanner", "ScannerFacade", "ScannerResult",
    "__version__",
]
