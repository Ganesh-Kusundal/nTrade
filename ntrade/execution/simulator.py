"""SimulatedExecution — fills order intents against instrument quote state.

Used by paper trading, backtests and replays: no network, deterministic. Zero
parity: the exact same OrderEngine → ExecutionTarget flow runs live via
``BrokerExecution`` and here via this class — only the fill source differs.

Costs: slippage and commission are configurable models; statutory Indian
charges (STT / exchange / SEBI / GST / stamp, H6) default to the real
``IndianStatutoryCosts`` schedule so simulated PnL converges on live. Pass
``statutory=None`` for the explicit zero-cost opt-out. The product schedule
(equity/futures/options) is derived from each filled instrument's class, and
GST is charged on the actual per-fill commission; pass a custom
``IndianStatutoryCosts(product=..., delivery=True)`` to override (e.g.
delivery-equity backtests).

Delivery detection: an equity SELL whose position was entered on a previous
session date is re-priced on the delivery schedule (0.1% STT + delivery
stamp) instead of intraday — the same uplift is applied to the entry leg, so
overnight round trips are fully delivery-priced. Disable with
``delivery_detection=False``. Shorts (SELL-first) are not delivery-detected:
a short opened intraday and covered next session stays intraday-priced on
both legs.
"""

from __future__ import annotations

from ntrade.events.order import (
    OrderAcceptedEvent,
    OrderFilledEvent,
    OrderIntentEvent,
    OrderRejectedEvent,
)
from ntrade.execution.costs import (
    CommissionModel,
    FlatCommission,
    IndianStatutoryCosts,
    SlippageModel,
    STATUTORY_DEFAULT,
    resolve_statutory,
)


class SimulatedExecution:
    name = "simulated"

    def __init__(
        self,
        context,
        *,
        slippage: SlippageModel | None = None,
        commission: CommissionModel | None = None,
        statutory=STATUTORY_DEFAULT,
        delivery_detection: bool = True,
    ):
        from datetime import date

        from ntrade.execution.costs import FixedSlippage

        self.ctx = context
        self.slippage = slippage or FixedSlippage(0.0)
        self.commission = commission or FlatCommission(0.0)
        # None → zero-cost opt-out; STATUTORY_DEFAULT → IndianStatutoryCosts().
        self.statutory: IndianStatutoryCosts | None = resolve_statutory(statutory)
        self.delivery_detection = delivery_detection
        # Entry session-date + notional per symbol, for overnight (delivery)
        # detection on the closing sell (feature: delivery-equity costs).
        self._entry_date: dict[str, date] = {}
        self._entry_notional: dict[str, float] = {}
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
        notional = fill_price * intent.quantity
        commission = round(self.commission.apply(notional), 4)
        # --- delivery (overnight) detection ---------------------------------
        held_overnight = False
        delivery_adjustment = 0.0
        if intent.side == "BUY":
            if self.delivery_detection:
                self._entry_date[intent.symbol] = intent.ts.date()
                self._entry_notional[intent.symbol] = notional
        elif self.delivery_detection and self.statutory is not None:
            entry = self._entry_date.get(intent.symbol)
            if entry is not None and intent.ts.date() > entry:
                # Position entered on a previous session date → the round trip
                # is delivery: sell leg on the delivery schedule plus the
                # buy-leg uplift (delivery buy STT + stamp vs intraday).
                held_overnight = True
                intraday = self.statutory.for_instrument(instrument)
                delivery = self.statutory.for_instrument(instrument, delivery=True)
                entry_notional = self._entry_notional.get(intent.symbol, 0.0)
                delivery_adjustment = round(
                    delivery.stt(entry_notional, "BUY")
                    + delivery.stamp(entry_notional, "BUY")
                    - intraday.stt(entry_notional, "BUY")
                    - intraday.stamp(entry_notional, "BUY"), 4)
        # --- statutory charges ----------------------------------------------
        if self.statutory is not None:
            # Product schedule from the instrument class (F&O vs equity), GST
            # on the actual per-fill commission, and the delivery schedule for
            # overnight equity exits — the details that would otherwise make
            # simulated charges diverge from live.
            model = self.statutory.for_instrument(instrument, delivery=held_overnight)
            statutory = round(
                model.total_cost(notional, intent.side, brokerage=commission)
                + delivery_adjustment, 4)
        else:
            statutory = 0.0
        filled = OrderFilledEvent(
            order_id=order_id, symbol=intent.symbol, exchange=intent.exchange,
            side=intent.side, quantity=intent.quantity, fill_price=round(fill_price, 4),
            commission=commission, statutory=statutory, strategy=intent.strategy, ts=intent.ts,
        )
        self.ctx.bus.publish(filled)
        self.fills.append(filled)
        return None
