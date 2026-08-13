"""Engines — the kernel's event-processing subsystems."""

# Importing strategies fires the indicator+strategy registry side-effects
# (register(...) calls), so the registries are populated whenever the engines
# package is imported — no explicit registration call needed at call sites.
from ntrade.engines import strategies  # noqa: F401  (side-effect: registration)
from ntrade.engines.candle_engine import CandleEngine
from ntrade.engines.indicator_engine import IndicatorEngine
from ntrade.engines.market_engine import MarketEngine
from ntrade.engines.order_engine import OrderEngine
from ntrade.engines.portfolio_engine import PortfolioEngine
from ntrade.engines.position_sync import PositionSyncEngine
from ntrade.engines.risk_engine import RiskEngine
from ntrade.engines.strategy_engine import Strategy, StrategyEngine

__all__ = [
    "MarketEngine", "CandleEngine", "IndicatorEngine",
    "StrategyEngine", "Strategy", "RiskEngine", "OrderEngine", "PortfolioEngine",
    "PositionSyncEngine",
]
