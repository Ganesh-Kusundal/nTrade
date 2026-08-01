"""M1 fix: partial exits keep the position's entry price (G2-F1)."""
from ntrade.domain.instruments.cash import Equity
from ntrade.events.order import OrderFilledEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    k.register(Equity("NIFTY"))
    return k


def _fill(k, side, qty, price, order_id):
    k.bus.publish(OrderFilledEvent(
        order_id=order_id, symbol="NIFTY", exchange="NSE", side=side,
        quantity=qty, fill_price=price, ts=k.clock.now()))


def test_partial_exit_keeps_avg_price():
    k = _kernel()
    _fill(k, "BUY", 10, 100.0, "o1")
    _fill(k, "SELL", 3, 105.0, "o2")  # partial exit of a long
    pos = k.ctx.portfolio.position("NIFTY")
    assert pos.quantity == 7
    assert pos.avg_price == 100.0      # was 105.0 (residual re-priced)
    assert pos.pnl == 35.0             # (105 - 100) * 7


def test_partial_exit_of_short_keeps_avg_price():
    k = _kernel()
    _fill(k, "SELL", 10, 100.0, "o1")
    _fill(k, "BUY", 4, 95.0, "o2")    # partial exit of a short
    pos = k.ctx.portfolio.position("NIFTY")
    assert pos.quantity == -6
    assert pos.avg_price == 100.0      # was 95.0 (residual re-priced)


def test_exit_and_reverse_resets_avg_price():
    k = _kernel()
    _fill(k, "BUY", 10, 100.0, "o1")
    _fill(k, "SELL", 12, 110.0, "o2")  # exit AND reverse into a short
    pos = k.ctx.portfolio.position("NIFTY")
    assert pos.quantity == -2
    assert pos.avg_price == 110.0      # fresh short entry price
