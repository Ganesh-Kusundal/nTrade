"""PortfolioEngine — maintains the Portfolio/Account read models from fills.

Consumes OrderFilledEvent: nets the position, re-averages the entry price,
debits/credits cash (notional + commission), and broadcasts
PositionUpdatedEvent + BalanceChangedEvent.
"""

from __future__ import annotations

from ntrade.domain.portfolio import Position
from ntrade.events.order import OrderFilledEvent
from ntrade.events.portfolio import BalanceChangedEvent, PositionUpdatedEvent


class PortfolioEngine:
    def __init__(self, context):
        self.ctx = context
        context.bus.subscribe(OrderFilledEvent, self.on_filled)

    def on_filled(self, event: OrderFilledEvent) -> None:
        portfolio = self.ctx.portfolio
        position = portfolio.position(event.symbol)
        qty = event.quantity if event.side == "BUY" else -event.quantity
        if position is None:
            position = Position(
                symbol=event.symbol, quantity=qty, avg_price=event.fill_price,
                ltp=event.fill_price, exchange=event.exchange,
                metadata={"strategy": event.strategy} if event.strategy else {},
            )
            portfolio.positions.append(position)
        else:
            new_qty = position.quantity + qty
            if new_qty == 0:
                portfolio.positions.remove(position)
                position = None
            else:
                same_direction = (position.quantity > 0) == (qty > 0)
                if same_direction:
                    total_cost = position.avg_price * position.quantity + event.fill_price * qty
                    position.avg_price = abs(total_cost / new_qty)
                elif position.quantity * new_qty < 0:
                    # exit-and-reverse: the residual is a fresh opposite position
                    position.avg_price = event.fill_price
                # else: partial exit (same sign) — keep the entry price
                position.quantity = new_qty
                position.ltp = event.fill_price

        notional = event.fill_price * event.quantity
        # Statutory charges (STT/exchange/SEBI/GST/stamp) are deducted from
        # cash on every leg, exactly like a live broker payout (H6).
        charges = event.commission + event.statutory
        if event.side == "BUY":
            self.ctx.account.balance = round(
                self.ctx.account.balance - notional - charges, 4)
        else:
            self.ctx.account.balance = round(
                self.ctx.account.balance + notional - charges, 4)

        remaining = portfolio.position(event.symbol) if position is not None else None
        self.ctx.bus.publish(PositionUpdatedEvent(
            symbol=event.symbol, exchange=event.exchange,
            quantity=remaining.quantity if remaining else 0,
            avg_price=remaining.avg_price if remaining else 0.0,
            ltp=event.fill_price, ts=event.ts,
        ))
        self.ctx.bus.publish(BalanceChangedEvent(
            balance=self.ctx.account.balance, ts=event.ts,
        ))
