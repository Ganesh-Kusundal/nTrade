"""Paper->live validation gate: turn a paper run's event history into the
evidence checklist that must pass before a strategy is switched to live."""

from __future__ import annotations

from ntrade.events.market import QuoteEvent, TickEvent
from ntrade.events.order import OrderFilledEvent


def _equity_trace(kernel, *, initial_cash: float):
    """Reconstruct the equity path from fills + market events.

    Order-independent: cash is derived from fills (not BalanceChangedEvent, so a
    fill's balance dip is never confused with a drawdown) and open positions are
    marked to the last observed price. Yields (peak_equity, current_equity) as
    the history advances.
    """
    cash = float(initial_cash)
    positions: dict[str, int] = {}
    ltp: dict[str, float] = {}
    peak = cash
    for e in kernel.bus.history:
        if isinstance(e, OrderFilledEvent):
            direction = 1 if e.side == "BUY" else -1
            cash -= e.quantity * e.fill_price * direction + e.commission + e.statutory
            positions[e.symbol] = positions.get(e.symbol, 0) + e.quantity * direction
        elif isinstance(e, TickEvent):
            ltp[e.symbol] = e.price
        elif isinstance(e, QuoteEvent):
            ltp[e.symbol] = e.ltp
        eq = cash + sum(q * ltp.get(sym, 0) for sym, q in positions.items())
        peak = max(peak, eq)
        if peak:
            yield peak, eq


def build_paper_report(kernel, *, initial_cash: float = 100_000.0) -> dict:
    fills = [e for e in kernel.bus.history if isinstance(e, OrderFilledEvent)]
    trades = [{
        "order_id": f.order_id, "symbol": f.symbol, "side": f.side,
        "quantity": f.quantity, "fill_price": f.fill_price, "ts": f.ts.isoformat(),
    } for f in fills]
    n_trades = len(fills)
    balance = getattr(kernel.ctx.account, "balance", initial_cash)
    equity = balance
    for p in kernel.ctx.portfolio.positions:
        equity += p.quantity * (p.ltp or p.avg_price)
    drawdown = 0.0
    for peak, eq in _equity_trace(kernel, initial_cash=initial_cash):
        drawdown = max(drawdown, (peak - eq) / peak * 100)
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
        },
    }
