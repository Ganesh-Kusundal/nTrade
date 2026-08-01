"""Backtest — bar-driven zero-parity simulations over the standard kernel."""

from ntrade.backtest.fills import FillPolicy
from ntrade.backtest.simulator import BacktestResult, BacktestSimulator

__all__ = ["BacktestSimulator", "BacktestResult", "FillPolicy"]
