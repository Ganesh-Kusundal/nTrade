"""Fill policies — decide how an order intent fills against historical bars.

The zero-parity architecture keeps fills inside the execution target; these
policies are the bar-aware decisions a backtest uses when the market engine is
driven by OHLCV bars instead of ticks.
"""

from __future__ import annotations

from dataclasses import replace

from ntrade.events.order import OrderIntentEvent, OrderRejectedEvent
from ntrade.execution.simulator import SimulatedExecution


class FillPolicy:
    """Standard bar-based fill rules (open/close/market/limit)."""

    def __init__(self, market_on: str = "close"):
        if market_on not in ("open", "close"):
            raise ValueError("market_on must be 'open' or 'close'")
        self.market_on = market_on

    def market_price(self, bar) -> float:
        """Market orders fill at the configured bar price."""
        return float(bar[self.market_on])

    def limit_fill(self, intent, bar) -> float | None:
        """Return the fill price for a limit intent, or None if not touched.

        A buy limit fills when the bar's low <= limit; a sell limit fills when
        the bar's high >= limit. Fill price is the better of limit and open.
        """
        limit = float(intent.price)
        if intent.side == "BUY":
            if float(bar["low"]) <= limit:
                return min(limit, float(bar["open"]))
            return None
        if float(bar["high"]) >= limit:
            return max(limit, float(bar["open"]))
        return None


class BarAwareExecution(SimulatedExecution):
    """Simulated execution whose LIMIT orders only fill when a bar trades through.

    Wraps ``SimulatedExecution`` (same fill/cost pipeline — zero parity) but
    consults a ``FillPolicy`` against the bar currently being processed.
    ``bar_provider`` is a callable returning the current bar dict (with
    open/high/low/close); MARKET orders behave exactly like the parent.
    """

    def __init__(self, context, *, policy: FillPolicy | None = None,
                 bar_provider=None, **kwargs):
        super().__init__(context, **kwargs)
        self.policy = policy or FillPolicy()
        self.bar_provider = bar_provider or (lambda: None)

    def submit(self, intent: OrderIntentEvent) -> OrderRejectedEvent | None:
        if intent.order_type != "MARKET":
            bar = self.bar_provider()
            if bar is not None:
                price = self.policy.limit_fill(intent, bar)
                if price is None:
                    return OrderRejectedEvent(
                        symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                        quantity=intent.quantity, reason="limit not touched",
                        strategy=intent.strategy, ts=intent.ts,
                    )
                intent = replace(intent, price=price)  # fill at bar-aware price
        return super().submit(intent)
