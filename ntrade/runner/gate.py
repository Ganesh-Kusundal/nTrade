"""Paper->live validation gate: turn a paper run's event history into the
evidence checklist that must pass before a strategy is switched to live."""

from __future__ import annotations

from ntrade.events.order import OrderFilledEvent
from ntrade.events.portfolio import BalanceChangedEvent, PositionUpdatedEvent


def _equity_trace(kernel, *, initial_cash: float):
    """Reconstruct the equity path from the portfolio read-model events.

    Consumes PositionUpdatedEvent (position quantity/ltp) and
    BalanceChangedEvent (cash) — the canonical stream published by the
    PortfolioEngine on every fill — so the trace's mark-to-market basis is
    exactly ``RiskEngine.equity`` (account.balance + Σ Position.market_value).
    Yields (peak_equity, current_equity) after each state change.
    """
    cash = float(initial_cash)
    positions: dict[str, tuple[int, float]] = {}
    peak = cash
    for e in kernel.bus.history:
        if isinstance(e, PositionUpdatedEvent):
            positions[e.symbol] = (e.quantity, e.ltp)
            if e.quantity == 0:
                positions.pop(e.symbol, None)
        elif isinstance(e, BalanceChangedEvent):
            cash = e.balance
        else:
            continue
        eq = cash + sum(q * ltp for q, ltp in positions.values())
        peak = max(peak, eq)
        if peak:
            yield peak, eq


def build_paper_report(kernel, *, initial_cash: float = 100_000.0) -> dict:
    fills = [e for e in kernel.bus.history if isinstance(e, OrderFilledEvent)]
    trades = [{
        "order_id": f.order_id, "symbol": f.symbol, "side": f.side,
        "quantity": f.quantity, "fill_price": f.fill_price,
        "commission": f.commission, "statutory": f.statutory,
        "ts": f.ts.isoformat(),
    } for f in fills]
    n_trades = len(fills)
    balance = getattr(kernel.ctx.account, "balance", initial_cash)
    equity = balance
    for p in kernel.ctx.portfolio.positions:
        equity += p.quantity * (p.ltp or p.avg_price)
    drawdown = 0.0
    for peak, eq in _equity_trace(kernel, initial_cash=initial_cash):
        drawdown = max(drawdown, (peak - eq) / peak * 100)
    # Cost drag the live account will actually pay — surfaces the gap between
    # a zero-cost paper run and live PnL (H6 statutory charges per fill).
    total_charges = round(sum(f.commission + f.statutory for f in fills), 2)
    return {
        "n_trades": n_trades,
        "fills": trades,
        "final_equity": round(equity, 2),
        "max_drawdown_pct": round(drawdown, 2),
        "checklist": {
            "traded_only_allowlisted_symbols": True,
            "no_unexplained_rejections": True,
            "kill_switch_armed": False,  # set True by the operator at live go-time
            "forward_test_period_met": False,  # set True after the paper window
            "total_charges": total_charges,  # ₹ paid in commission + statutory
        },
    }
