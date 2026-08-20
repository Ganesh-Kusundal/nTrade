"""OrderEngine (OMS) — materialises approved signals into order intents.

Consumes SignalApprovedEvent, builds an OrderIntentEvent, submits it to the
execution router (simulated / broker), and republishes any OrderRejectedEvent.
Acceptance and fills are published by the execution target itself.
"""

from __future__ import annotations

from ntrade.events.order import OrderIntentEvent, OrderRejectedEvent
from ntrade.events.risk import SignalApprovedEvent


class OrderEngine:
    def __init__(self, context, router):
        self.ctx = context
        self.router = router
        self._intents = 0
        context.bus.subscribe(SignalApprovedEvent, self.on_signal_approved)

    def on_signal_approved(self, event: SignalApprovedEvent) -> None:
        signal = event.signal
        self._intents += 1
        intent = OrderIntentEvent(
            symbol=signal.symbol, exchange=signal.exchange, side=signal.side,
            quantity=signal.quantity,
            # Preserve backward compatibility: LIMIT when price is set,
            # MARKET when price is zero — but allow explicit order_type from
            # the new signal.order_type field.
            order_type=signal.order_type if signal.order_type != "MARKET"
            else ("LIMIT" if signal.price else "MARKET"),
            price=signal.price,
            reference_price=signal.metadata.get("reference_price", 0.0),
            strategy=signal.strategy, ts=event.ts,
        )
        self.ctx.bus.publish(intent)
        outcome = self.router.submit(intent)
        if isinstance(outcome, OrderRejectedEvent):
            self.ctx.bus.publish(outcome)
