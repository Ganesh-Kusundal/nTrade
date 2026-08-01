"""Cost models — slippage and commission applied by simulated execution.

Lives in ``execution/`` (not ``backtest/``) because simulated execution is
shared by live paper trading, replay and backtest — zero parity demands the
same cost pipeline everywhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SlippageModel(ABC):
    @abstractmethod
    def apply(self, price: float, side: str) -> float:
        """Return the executed price for a BUY/SELL order."""


class FixedSlippage(SlippageModel):
    def __init__(self, points: float = 0.0):
        self.points = points

    def apply(self, price: float, side: str) -> float:
        return price + self.points if side == "BUY" else price - self.points


class PercentageSlippage(SlippageModel):
    def __init__(self, pct: float = 0.0):
        self.pct = pct

    def apply(self, price: float, side: str) -> float:
        return price * (1 + self.pct) if side == "BUY" else price * (1 - self.pct)


class CommissionModel(ABC):
    @abstractmethod
    def apply(self, notional: float) -> float:
        """Commission charged on an order notional."""


class FlatCommission(CommissionModel):
    def __init__(self, amount: float = 0.0):
        self.amount = amount

    def apply(self, notional: float) -> float:
        return self.amount


class PercentageCommission(CommissionModel):
    def __init__(self, pct: float = 0.0, minimum: float = 0.0):
        self.pct = pct
        self.minimum = minimum

    def apply(self, notional: float) -> float:
        return max(notional * self.pct, self.minimum)
