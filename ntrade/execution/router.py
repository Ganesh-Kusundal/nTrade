"""ExecutionRouter — routes order intents to execution targets.

Targets are registered by name (strategy names, or ``default``); the router
picks the target matching the intent's strategy, falling back to ``default``.
This is the interchangeable "execution target" of the zero-parity invariant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ntrade.events.order import OrderRejectedEvent

if TYPE_CHECKING:
    from ntrade.events.order import OrderIntentEvent
    from ntrade.kernel.context import TradingContext


class ExecutionRouter:
    def __init__(self, context: "TradingContext"):
        self.ctx = context
        self._targets: dict[str, object] = {}
        self._default: str | None = None

    def add(self, name: str, target) -> "ExecutionRouter":
        self._targets[name] = target
        return self

    def default(self, name: str) -> "ExecutionRouter":
        self._default = name
        return self

    def targets(self) -> list:
        """Registered execution targets (values of the name→target map)."""
        return list(self._targets.values())

    def submit(self, intent: "OrderIntentEvent"):
        target = self._targets.get(intent.strategy)
        if target is None and self._default is not None:
            target = self._targets.get(self._default)
        if target is None:
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity,
                reason=f"no execution target for strategy {intent.strategy!r}",
                strategy=intent.strategy, ts=intent.ts,
            )
        return target.submit(intent)
