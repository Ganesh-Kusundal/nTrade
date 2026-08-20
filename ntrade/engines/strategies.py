"""Reusable strategies built on the kernel's canonical events.

Strategies live in ``Strategy`` subclass form: they react to events through the
standard hooks and emit signals via ``emit_signal()``, so they run identically
in live (BrokerExecution) and backtest (BacktestSimulator).
"""

from __future__ import annotations

import logging
import pandas as pd


from ntrade.engines.strategy_engine import Strategy
from ntrade.registry import StrategySpec, strategy



logger = logging.getLogger("ntrade.strategy.halftrend")


class HalfTrendStrategy(Strategy):
    """HalfTrend bar-replay strategy — signal markers for chart overlays.

    This is the engine-side face of the domain HalfTrend indicator. It keeps
    only the per-candle replay needed to surface buy/sell markers through the
    overlay pipeline; the full indicator math lives in
    ``ntrade.domain.analytics.halftrend`` and runs on the chart backend.
    """

    name = "halftrend"

    def __init__(self, symbol: str | None = None, exchange: str | None = None,
                 amplitude: int = 2, channel_deviation: int = 2,
                 atr_period: int = 100, lot_size: int = 1,
                 strategy_params: dict | None = None):
        super().__init__()
        self.symbol = symbol
        self.exchange = exchange
        self.lot_size = int(lot_size)
        self.amplitude = int(amplitude)
        self.channel_deviation = int(channel_deviation)
        self.atr_period = int(atr_period)
        self.strategy_params = dict(strategy_params or {})
        self._ht: list[float] = []
        self._trend: list[int] = []
        self._buy: list[bool] = []
        self._sell: list[bool] = []

    def _bind(self, df):
        from ntrade.domain.analytics.halftrend import halftrend as _ht
        out = _ht(df, amplitude=self.amplitude,
                  channel_deviation=self.channel_deviation,
                  atr_period=self.atr_period)
        if out.empty:
            self._ht = []
            self._trend = []
            self._buy = []
            self._sell = []
            return
        self._ht = out["ht"].tolist()
        self._trend = out["trend"].fillna(0).astype(int).tolist()
        self._buy = out["buySignal"].tolist()
        self._sell = out["sellSignal"].tolist()

    def latest_signal(self, close: float) -> dict | None:
        if not self._buy or not self._sell:
            return None
        n = len(self._buy)
        if n == 0:
            return None
        idx = n - 1
        if self._buy[idx]:
            return {"side": "BUY", "price": float(close),
                    "ht": float(self._ht[idx]) if self._ht[idx] != self._ht[idx] else None}
        if self._sell[idx]:
            return {"side": "SELL", "price": float(close),
                    "ht": float(self._ht[idx]) if self._ht[idx] != self._ht[idx] else None}
        return None

    def on_candle_closed(self, event) -> None:
        if self.symbol is not None and event.symbol != self.symbol:
            return
        # Build OHLCV frame from the replay buffer; markers are computed
        # lazily by the overlay pipeline, so this hook is intentionally light.
        pass








# Implementation lookup: maps a strategy id (the registry key / StrategySpec.id)
# to the concrete class. The registry is the contract; this dict is the plumbing
# that turns a spec into a live instance. New strategy → one register() + one
# _strategy_classes line, zero call-site edits elsewhere.
_strategy_classes = {
    "halftrend": HalfTrendStrategy,
}


# Register each strategy as a spec (id + default-constructor params + the
# indicator ids it reads). Registration happens at import time so callers only
# need to import `strategy` — the contract is populated by the side-effect.
strategy.register("halftrend", StrategySpec(
    id="halftrend", label="HalfTrend",
    params={"amplitude": 2, "channel_deviation": 2, "atr_period": 100},
    indicators=["halftrend"],
))