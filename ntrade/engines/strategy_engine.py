"""StrategyEngine — fans kernel events out to registered strategies.

Strategies never poll; they react to events through hooks and emit signals via
``emit_signal()``. The engine dispatches each subscribed event type to every
registered strategy; a failing strategy is logged (with traceback) and its
error counter bumped, but never takes down sibling strategies.
"""

from __future__ import annotations

import logging

from ntrade.domain.constants import Exchange
from ntrade.events.market import (
    CandleClosedEvent, IndicatorUpdatedEvent, QuoteUpdatedEvent, TickEvent,
    WatchlistReady,
)
from ntrade.events.order import OrderFilledEvent
from ntrade.events.portfolio import BalanceChangedEvent, PositionUpdatedEvent
from ntrade.events.risk import SignalGeneratedEvent

logger = logging.getLogger("ntrade.strategy")


class Strategy:
    """Base class — override the hooks you care about; emit signals to act."""

    name = "strategy"

    def __init__(self):
        self.ctx = None
        self.enabled = True

    # --------------------------------------------------------- hooks (override)
    def on_tick(self, event): ...
    def on_quote_updated(self, event): ...
    def on_candle_closed(self, event): ...
    def on_indicator_updated(self, event): ...
    def on_watchlist(self, event): ...  # screener run complete
    def on_position_updated(self, event): ...
    def on_order_filled(self, event): ...
    def on_balance_changed(self, event): ...

    # --------------------------------------------------------------- helpers
    def emit_signal(self, *, symbol, exchange: str = Exchange.CASH, side: str,
                    quantity: int, price: float = 0.0, reference_price: float = 0.0,
                    order_type: str = "MARKET", **metadata) -> SignalGeneratedEvent:
        """Emit a trade signal; RiskEngine screens it before it becomes an order.

        ``reference_price`` is the bar-close price that triggered a signal
        (carried through to fills for zero-parity across backtest/replay/
        paper); 0.0 means "use the live LTP". ``order_type`` lets the
        strategy express fill intent (LIMIT at bar close, or MARKET).
        """
        if reference_price:
            metadata["reference_price"] = reference_price
        signal = SignalGeneratedEvent(
            symbol=symbol, exchange=exchange, side=side, quantity=quantity,
            price=price, order_type=order_type, strategy=self.name,
            metadata=metadata, ts=self.ctx.now(),
        )
        self.ctx.bus.publish(signal)
        return signal


class StrategyEngine:
    _HOOKS = {
        TickEvent: "on_tick",
        QuoteUpdatedEvent: "on_quote_updated",
        CandleClosedEvent: "on_candle_closed",
        IndicatorUpdatedEvent: "on_indicator_updated",
        WatchlistReady: "on_watchlist",
        PositionUpdatedEvent: "on_position_updated",
        OrderFilledEvent: "on_order_filled",
        BalanceChangedEvent: "on_balance_changed",
    }

    def __init__(self, context):
        self.ctx = context
        self.strategies: list[Strategy] = []
        for event_type, hook in self._HOOKS.items():
            context.bus.subscribe(event_type, self._dispatch(hook))

    # strategy lifecycle is orthogonal to scanner throttling (never reset ScannerFacade's
    # rate-limit cache) see tests/test_contract_strategy_scanner_orthogonal.py
    def register(self, strategy: Strategy) -> Strategy:
        strategy.ctx = self.ctx
        self.strategies.append(strategy)
        return strategy

    def remove(self, strategy) -> bool:
        """Detach a strategy by identity or name (hot detach)."""
        for existing in list(self.strategies):
            if existing is strategy or getattr(existing, "name", "") == strategy:
                self.strategies.remove(existing)
                return True
        return False

    def names(self) -> list[str]:
        return [s.name for s in self.strategies]

    def _dispatch(self, hook: str):
        def handler(event):
            for strategy in list(self.strategies):
                if not getattr(strategy, "enabled", True):
                    continue
                fn = getattr(strategy, hook, None)
                if fn is None:
                    continue
                try:
                    fn(event)
                except Exception:
                    # Never silent: a broken live strategy must be visible.
                    # The hook is isolated so sibling strategies keep running.
                    strategy._error_count = getattr(strategy, "_error_count", 0) + 1
                    logger.exception(
                        "strategy %s.%s failed on %s (errors=%d)",
                        getattr(strategy, "name", strategy), hook,
                        type(event).__name__, strategy._error_count,
                    )
        return handler
