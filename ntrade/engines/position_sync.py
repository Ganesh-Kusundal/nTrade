"""PositionSyncEngine — reconciles broker-reported state into the kernel.

Live mode: the broker is the source of truth for positions and cash (fills may
arrive from other terminals, manual trades, corporate actions). ``sync()``
pulls ``broker.get_positions()`` / ``broker.get_balance()`` and reconciles the
kernel's Portfolio/Account read models, publishing ``PositionUpdatedEvent`` and
``BalanceChangedEvent`` so the rest of the kernel (recording, strategies)
observes the same canonical events as it would for an internal fill.

Failure safety: a transient broker error must never wipe the portfolio or zero
the account — a failed fetch is skipped and the previous state is kept, since
"flat" and "error" are both observable as empty/zero results and dropping real
positions on a network hiccup would corrupt the read models.
"""

from __future__ import annotations

from ntrade.domain.portfolio import Position
from ntrade.domain.constants import Exchange
from ntrade.events.portfolio import BalanceChangedEvent, PositionUpdatedEvent


class PositionSyncEngine:
    def __init__(self, context, broker):
        self.ctx = context
        self.broker = broker

    # ---------------------------------------------------------------- reconcile
    def sync(self) -> int:
        """Reconcile broker positions/balance into the kernel. Returns the
        number of positions after reconciliation."""
        reported = self._safe_positions()
        if reported is None:
            return len(self.ctx.portfolio.positions)  # transient failure: keep state
        by_symbol = {p.symbol: p for p in reported}
        portfolio = self.ctx.portfolio

        # upsert positions reported by the broker
        for symbol, bp in by_symbol.items():
            local = portfolio.position(symbol)
            if local is None:
                local = Position(
                    symbol=symbol, quantity=0, avg_price=0.0,
                    ltp=bp.ltp, product=bp.product or "MIS",
                    exchange=bp.exchange or Exchange.CASH,
                    metadata=self._strategy_meta(bp),
                )
                portfolio.positions.append(local)
            elif bp.metadata and "strategy" in bp.metadata:
                # broker reports who opened it; prefer that in live
                local.metadata = {"strategy": bp.metadata["strategy"]}
            quantity = int(bp.quantity or 0)
            avg_price = _safe_float(bp.avg_price)
            ltp = _safe_float(bp.ltp)
            changed = (local.quantity != quantity or local.avg_price != avg_price
                       or local.ltp != ltp)
            local.quantity, local.avg_price, local.ltp = quantity, avg_price, ltp
            if changed:
                self.ctx.bus.publish(PositionUpdatedEvent(
                    symbol=symbol, exchange=local.exchange,
                    quantity=quantity, avg_price=avg_price,
                    ltp=ltp, ts=self.ctx.now(),
                ))

        # drop local positions the broker no longer reports
        for local in list(portfolio.positions):
            if local.symbol not in by_symbol:
                portfolio.positions.remove(local)
                self.ctx.bus.publish(PositionUpdatedEvent(
                    symbol=local.symbol, exchange=local.exchange,
                    quantity=0, avg_price=0.0, ltp=0.0, ts=self.ctx.now(),
                ))

        # reconcile cash (broker-reported balance is authoritative in live)
        balance = self._safe_balance()
        if balance is not None and balance != self.ctx.account.balance:
            self.ctx.account.balance = round(balance, 4)
            self.ctx.bus.publish(BalanceChangedEvent(
                balance=self.ctx.account.balance, ts=self.ctx.now(),
            ))
        return len(portfolio.positions)

    @staticmethod
    def _strategy_meta(bp) -> dict:
        if bp.metadata and "strategy" in bp.metadata:
            return {"strategy": bp.metadata["strategy"]}
        return {}

    def _safe_positions(self) -> list | None:
        try:
            return self.broker.get_positions() or []
        except Exception:
            return None  # transient failure: keep the current portfolio

    def _safe_balance(self) -> float | None:
        try:
            value = self.broker.get_balance()
        except Exception:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def _safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
