"""Reusable strategies built on the kernel's canonical events.

Strategies live in ``Strategy`` subclass form: they react to events through the
standard hooks and emit signals via ``emit_signal()``, so they run identically
in live (BrokerExecution), replay (ReplayEngine) and backtest (BacktestSimulator).
"""

from __future__ import annotations

from ntrade.engines.strategy_engine import Strategy


class EmaCrossStrategy(Strategy):
    """EMA fast/slow crossover — the canonical momentum strategy.

    Golden cross (fast EMA crosses above slow) → BUY; death cross (fast crosses
    below slow) → SELL. Position-aware so it holds one direction at a time
    (reverses instead of stacking) and only acts on an actual crossing, not a
    crossover that keeps its direction.

    Reads the EMA values from the indicator bundle projected onto the instrument
    by the IndicatorEngine (needs ``compute_bundle(ema_periods=(fast, slow))``),
    falling back to computing the EMAs itself when the bundle is missing.
    """

    name = "ema_cross"

    def __init__(self, fast: int = 9, slow: int = 21, quantity: int = 5,
                 symbol: str | None = None):
        super().__init__()
        self.fast = int(fast)
        self.slow = int(slow)
        self.quantity = int(quantity)
        self.symbol = symbol  # None = trade every candle
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    # ------------------------------------------------------------------ hook
    def on_candle_closed(self, event) -> None:
        if self.symbol is not None and event.symbol != self.symbol:
            return
        bundle = self.ctx.instrument(event.symbol)._indicators
        fast = bundle.get(f"ema_{self.fast}")
        slow = bundle.get(f"ema_{self.slow}")
        if fast is None or slow is None:
            return  # warm-up: not enough candles for both EMAs yet
        if self._prev_fast is None or self._prev_slow is None:
            self._prev_fast, self._prev_slow = fast, slow
            return  # need one prior reading to detect a crossing

        crossed_up = self._prev_fast <= self._prev_slow and fast > slow
        crossed_down = self._prev_fast >= self._prev_slow and fast < slow
        self._prev_fast, self._prev_slow = fast, slow
        if not crossed_up and not crossed_down:
            return

        position = self.ctx.portfolio.position(event.symbol)
        qty = position.quantity if position is not None else 0
        # Always-in-market with reversal, never stacking: BUY covers flat + short
        # entries (golden cross), SELL covers flat + long exits (death cross).
        if crossed_up and qty <= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=event.close)
        elif crossed_down and qty >= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=self.quantity, price=event.close)
