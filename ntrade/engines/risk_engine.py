"""RiskEngine — screens signals before they become order intents.

Consumes SignalGeneratedEvent; publishes SignalApprovedEvent or
SignalRejectedEvent. Static limits (allowlist, quantity, notional, position
count) plus circuit breakers: a daily-loss cap, a max-drawdown halt and a
price-deviation (fat-finger) guard. Once halted, every signal is rejected
until resume(). The engine is broker-agnostic — the kill-switch side effect
lives in the LiveRunner, which reacts to RiskHaltedEvent.
"""

from __future__ import annotations

from ntrade.events.risk import (
    RiskHaltedEvent, RiskResumedEvent,
    SignalApprovedEvent, SignalGeneratedEvent, SignalRejectedEvent,
)


class RiskEngine:
    def __init__(self, context, *, max_quantity: int | None = None,
                 max_notional: float | None = None, max_positions: int | None = None,
                 allowlist: set | None = None, strategy: str | None = None,
                 max_daily_loss: float | None = None,
                 max_drawdown_pct: float | None = None,
                 price_deviation_pct: float | None = None):
        self.ctx = context
        self.max_quantity = max_quantity
        self.max_notional = max_notional
        self.max_positions = max_positions
        self.allowlist = set(allowlist) if allowlist else None
        self.strategy = strategy  # None = screen all strategies
        self.max_daily_loss = max_daily_loss
        self.max_drawdown_pct = max_drawdown_pct
        self.price_deviation_pct = price_deviation_pct
        self.halted = False
        self.halt_reason = ""
        self._start_balance = float(context.account.balance)
        self._peak_equity = None
        self.approved = 0
        self.rejected = 0
        context.bus.subscribe(SignalGeneratedEvent, self.on_signal)

    # ------------------------------------------------------------------ state
    def equity(self) -> float:
        """Session equity = cash balance + open-position mark-to-market."""
        mtm = sum(p.market_value for p in self.ctx.portfolio.positions)
        return float(self.ctx.account.balance) + mtm

    def halt(self, reason: str) -> None:
        if not self.halted:
            self.halted = True
            self.halt_reason = reason
            self.ctx.bus.publish(RiskHaltedEvent(
                reason=reason, equity=self.equity(), ts=self.ctx.now()))

    def resume(self) -> None:
        if self.halted:
            self.halted = False
            self.halt_reason = ""
            self._peak_equity = None
            self._start_balance = float(self.ctx.account.balance)
            self.ctx.bus.publish(RiskResumedEvent(ts=self.ctx.now()))

    def check(self) -> str | None:
        """Evaluate the circuit breakers now, returning the halt reason when
        tripped (else None). Unlike on_signal, this runs even with no signal in
        flight — the LiveRunner calls it every loop iteration so a bleeding
        position trips max_daily_loss / max_drawdown_pct mid-session."""
        self._update_breakers()
        return self.halt_reason if self.halted else None

    # ---------------------------------------------------------------- screening
    def on_signal(self, event: SignalGeneratedEvent) -> None:
        if self.strategy is not None and event.strategy != self.strategy:
            return  # not my strategy — a per-strategy RiskEngine must not touch it
        reason = self._check(event)
        if reason is None:
            self.approved += 1
            self.ctx.bus.publish(SignalApprovedEvent(signal=event, ts=event.ts))
        else:
            self.rejected += 1
            self.ctx.bus.publish(SignalRejectedEvent(signal=event, reason=reason, ts=event.ts))

    def _check(self, event: SignalGeneratedEvent) -> str | None:
        self._update_breakers()
        if self.halted:
            return f"risk halted: {self.halt_reason}"
        if self.allowlist is not None and event.symbol not in self.allowlist:
            return f"symbol {event.symbol!r} not in allowlist"
        if self.max_quantity is not None and event.quantity > self.max_quantity:
            return f"quantity {event.quantity} exceeds max {self.max_quantity}"
        notional = event.price * event.quantity
        if self.max_notional is not None and notional > self.max_notional:
            return f"notional {notional:.2f} exceeds max {self.max_notional}"
        if self.max_positions is not None:
            count = self._position_count(event)
            if count >= self.max_positions:
                return f"max positions {self.max_positions} reached"
        if self.price_deviation_pct is not None:
            instrument = self.ctx.instrument(event.symbol)
            ref = instrument._quote.ltp or None
            if not ref:
                ref = instrument._quote.prev_close or None
            if not ref:
                return (f"price {event.price:.2f} unverifiable: no market price "
                        f"for {event.symbol}")
            dev = abs(event.price - ref) / ref * 100
            if dev > self.price_deviation_pct:
                return (f"price {event.price:.2f} deviates {dev:.1f}% "
                        f"from ref {ref:.2f} (> {self.price_deviation_pct}%)")
        return None

    def _update_breakers(self) -> None:
        if self.halted:
            return
        equity = self.equity()
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        if self.max_daily_loss is not None:
            loss = self._start_balance - equity
            if loss > self.max_daily_loss:
                self.halt(f"daily loss {loss:.2f} exceeds cap {self.max_daily_loss}")
                return
        if self.max_drawdown_pct is not None and self._peak_equity:
            dd = (self._peak_equity - equity) / self._peak_equity * 100
            if dd > self.max_drawdown_pct:
                self.halt(f"drawdown {dd:.1f}% exceeds {self.max_drawdown_pct}%")

    def _position_count(self, event) -> int:
        """Number of open positions relevant to this engine.

        A per-strategy engine counts only positions opened by that strategy;
        the global engine (strategy=None) counts the whole portfolio.
        """
        if self.strategy is None:
            return len(self.ctx.portfolio.positions)
        return sum(
            1 for p in self.ctx.portfolio.positions
            if p.metadata.get("strategy") == self.strategy
        )
