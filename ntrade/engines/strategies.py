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
    """HalfTrend signal strategy — emits BUY/SELL on trend flips.

    The engine-side face of the domain HalfTrend indicator. Accumulates closed
    candles, runs ``halftrend()`` over the buffer on each bar close, and emits
    a signal when the latest bar produces a buy/sell marker. The full indicator
    math lives in ``ntrade.domain.analytics.halftrend`` (zero-parity: the same
    function the chart overlay runs).
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
        self._buf: list[dict] = []

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
        # Same warmup gate as the chart overlay: trend flips before atr_period
        # evolve on uninitialized ATR state — never trade them.
        if len(self._buy) < self.atr_period:
            return None
        if not self._buy or not self._sell:
            return None
        n = len(self._buy)
        if n == 0:
            return None
        idx = n - 1
        if self._buy[idx]:
            return {"side": "BUY", "price": float(close),
                    "ht": float(self._ht[idx]) if self._ht[idx] == self._ht[idx] else None}
        if self._sell[idx]:
            return {"side": "SELL", "price": float(close),
                    "ht": float(self._ht[idx]) if self._ht[idx] == self._ht[idx] else None}
        return None

    def on_candle_closed(self, event) -> None:
        if self.symbol is not None and event.symbol != self.symbol:
            return
        if getattr(event, "timeframe", "1m") != "1m":
            return  # mixed-timeframe streams would corrupt the bar buffer
        self._buf.append({
            "open": float(event.open),
            "high": float(event.high),
            "low": float(event.low),
            "close": float(event.close),
            "volume": float(event.volume),
        })
        self._bind(pd.DataFrame(self._buf))
        sig = self.latest_signal(float(event.close))
        if sig is None:
            return
        self.emit_signal(
            symbol=event.symbol,
            exchange=event.exchange,
            side=sig["side"],
            quantity=self.lot_size,
            price=float(event.close),
            reference_price=float(event.close),
            order_type="MARKET",
            ht=sig.get("ht"),
        )




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