"""SimulatedExecution — fills order intents against instrument quote state.

Used by paper trading, backtests and replays: no network, deterministic. Zero
parity: the exact same OrderEngine → ExecutionTarget flow runs live via
``BrokerExecution`` and here via this class — only the fill source differs.
"""

from __future__ import annotations

from ntrade.events.order import (
    OrderAcceptedEvent,
    OrderFilledEvent,
    OrderIntentEvent,
    OrderRejectedEvent,
)
from ntrade.execution.costs import CommissionModel, FlatCommission, SlippageModel


class SimulatedExecution:
    name = "simulated"

    def __init__(
        self,
        context,
        *,
        slippage: SlippageModel | None = None,
        commission: CommissionModel | None = None,
    ):
        from ntrade.execution.costs import FixedSlippage

        self.ctx = context
        self.slippage = slippage or FixedSlippage(0.0)
        self.commission = commission or FlatCommission(0.0)
        self._seq = 0
        self.fills: list[OrderFilledEvent] = []

    def submit(self, intent: OrderIntentEvent) -> OrderRejectedEvent | None:
        instrument = self.ctx.instrument(intent.symbol)
        if instrument is None:
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity, reason=f"unknown instrument {intent.symbol!r}",
                strategy=intent.strategy, ts=intent.ts,
            )
        if intent.order_type == "MARKET":
            base = instrument._quote.ltp or 0.0
            if base <= 0:
                return OrderRejectedEvent(
                    symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                    quantity=intent.quantity, reason="no market price available",
                    strategy=intent.strategy, ts=intent.ts,
                )
            fill_price = self.slippage.apply(base, intent.side)
        else:
            fill_price = intent.price or 0.0
        if fill_price <= 0:
            return OrderRejectedEvent(
                symbol=intent.symbol, exchange=intent.exchange, side=intent.side,
                quantity=intent.quantity, reason="invalid fill price",
                strategy=intent.strategy, ts=intent.ts,
            )
        self._seq += 1
        order_id = f"SIM-{self._seq:06d}"
        self.ctx.bus.publish(OrderAcceptedEvent(
            order_id=order_id, symbol=intent.symbol, exchange=intent.exchange,
            side=intent.side, quantity=intent.quantity, strategy=intent.strategy, ts=intent.ts,
        ))
        commission = round(self.commission.apply(fill_price * intent.quantity), 4)
        filled = OrderFilledEvent(
            order_id=order_id, symbol=intent.symbol, exchange=intent.exchange,
            side=intent.side, quantity=intent.quantity, fill_price=round(fill_price, 4),
            commission=commission, strategy=intent.strategy, ts=intent.ts,
        )
        self.ctx.bus.publish(filled)
        self.fills.append(filled)
        return None
