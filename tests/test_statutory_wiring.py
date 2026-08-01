"""Statutory-cost wiring (H6) — simulated fills deduct the full Indian charge.

STT / exchange transaction charges / SEBI fee / GST / stamp duty are charged
on every simulated leg by default so backtest/paper PnL converges on live.
Zero-cost remains an explicit opt-out (``statutory=None``).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent, OrderIntentEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.execution.costs import IndianStatutoryCosts, STATUTORY_DEFAULT, resolve_statutory
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _kernel(**kw) -> TradingKernel:
    kw.setdefault("mode", "replay")
    kw.setdefault("clock", ReplayClock())
    k = TradingKernel(**kw)
    k.register(Equity("RELIANCE", exchange="NSE"))
    return k


def _intent(side: str = "BUY", qty: int = 10, price: float = 100.0,
            order_type: str = "LIMIT", ts=None, symbol: str = "RELIANCE",
            exchange: str = "NSE") -> OrderIntentEvent:
    return OrderIntentEvent(
        symbol=symbol, exchange=exchange, side=side, quantity=qty,
        order_type=order_type, price=price, strategy="t",
        ts=ts or datetime(2026, 1, 1, 9, 15),
    )


def _fill(k) -> OrderFilledEvent:
    return [e for e in k.bus.history if isinstance(e, OrderFilledEvent)][0]


# ---------------------------------------------------------------- sentinel
def test_statutory_default_sentinel_resolves_to_model():
    assert isinstance(resolve_statutory(STATUTORY_DEFAULT), IndianStatutoryCosts)
    assert resolve_statutory(None) is None  # zero-cost opt-out stays explicit


# ---------------------------------------------------------------- fill level
def test_default_wiring_deducts_statutory_on_fill():
    k = _kernel()
    k.router.submit(_intent(side="BUY", qty=10, price=100.0))
    fill = _fill(k)
    expected = round(IndianStatutoryCosts().total_cost(100.0 * 10, "BUY"), 4)
    assert fill.statutory == pytest.approx(expected)
    assert fill.statutory > 0.0  # notional 1000 → stamp + exchange + sebi + gst


def test_zero_cost_is_explicit_opt_out():
    k = _kernel(statutory=None)
    k.router.submit(_intent())
    fill = _fill(k)
    assert fill.statutory == 0.0
    assert fill.commission == 0.0


def test_custom_statutory_model_used():
    custom = IndianStatutoryCosts(stt={"equity_intraday_sell": 0.002})  # 0.2% sell
    k = _kernel(statutory=custom)
    k.router.submit(_intent(side="SELL", qty=10, price=100.0))
    fill = _fill(k)
    expected = round(custom.total_cost(1000.0, "SELL"), 4)
    assert fill.statutory == pytest.approx(expected)
    assert fill.statutory > 0.0


def _direct_exec(commission=None, statutory=STATUTORY_DEFAULT):
    """A bare SimulatedExecution (kernel sim target takes no commission)."""
    from ntrade.execution.simulator import SimulatedExecution
    from ntrade.kernel.context import TradingContext
    from ntrade.kernel.event_bus import EventBus

    ctx = TradingContext(EventBus(), ReplayClock(), mode="replay")
    ctx.register(Equity("RELIANCE", exchange="NSE"))
    return SimulatedExecution(ctx, commission=commission, statutory=statutory), ctx


def test_gst_charged_on_configured_commission():
    """GST is 18% of brokerage too — the commission model's output must feed it."""
    from ntrade.execution.costs import PercentageCommission

    exec_, ctx = _direct_exec(commission=PercentageCommission(pct=0.01))
    exec_.submit(_intent(side="BUY", qty=10, price=100.0))
    fill = [e for e in ctx.bus.history if isinstance(e, OrderFilledEvent)][0]
    assert fill.commission == pytest.approx(10.0)  # 1% of ₹1000
    model = IndianStatutoryCosts()
    assert fill.statutory == pytest.approx(
        round(model.total_cost(1000.0, "BUY", brokerage=10.0), 4))
    # strictly more than ignoring brokerage → GST on commission is included
    assert fill.statutory > round(model.total_cost(1000.0, "BUY"), 4)


def test_fno_instrument_uses_fno_stt_schedule():
    """An Option fill defaults to the F&O schedule, not equity-intraday rates."""
    from datetime import date

    from ntrade.domain.instruments.derivatives import Option

    und = Equity("NIFTY")
    opt = Option("NIFTY 20000 CE 28Jan26", exchange="NFO", strike=20000.0,
                 expiry=date(2026, 1, 28), option_type="CE", underlying_symbol="NIFTY")
    opt.set_underlying(und)
    k = _kernel()
    k.register(opt)
    k.router.submit(_intent(side="SELL", qty=75, price=50.0,
                           symbol=opt.symbol, exchange="NFO"))
    fill = _fill(k)
    notional = 50.0 * 75
    # 0.125% F&O sell STT, 0.0503% exchange, 0.01% stamp — not equity intraday
    assert fill.statutory == pytest.approx(
        round(IndianStatutoryCosts(product="options").total_cost(notional, "SELL"), 4))
    assert fill.statutory > IndianStatutoryCosts().total_cost(notional, "SELL")


def test_for_instrument_keeps_equity_model():
    model = IndianStatutoryCosts()
    assert model.for_instrument(Equity("X")) is model
    assert model.for_instrument(Equity("X")) is not None


# ------------------------------------------------------------ portfolio level
def test_portfolio_balance_deducts_statutory_per_leg():
    k = _kernel(initial_cash=100_000.0)
    k.router.submit(_intent(side="BUY", qty=10, price=100.0))
    buy = _fill(k)
    assert k.ctx.account.balance == pytest.approx(
        round(100_000.0 - 1000.0 - buy.commission - buy.statutory, 4))

    k.router.submit(_intent(side="SELL", qty=10, price=110.0))
    sells = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
    sell = sells[-1]
    assert sell.statutory > 0.0
    total = sum(f.commission + f.statutory for f in sells)
    # closed flat → balance = initial + round-trip pnl − all charges
    assert k.ctx.account.balance == pytest.approx(
        round(100_000.0 + 10 * 10.0 - total, 4))


# ------------------------------------------------------------- convergence
class BuySellOnCandles(Strategy):
    name = "buy_sell"

    def __init__(self):
        super().__init__()
        self.count = 0

    def on_candle_closed(self, event):
        self.count += 1
        if self.count == 3:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=10, price=event.close)
        elif self.count == 8:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=10, price=event.close)


def _ohlcv(bars: int = 20, step_price: float = 1.0) -> pd.DataFrame:
    start = datetime(2026, 1, 1, 9, 15)
    rows = []
    for i in range(bars):
        rows.append({
            "timestamp": start + timedelta(minutes=5 * i),
            "open": 100 + i * step_price, "high": 102 + i * step_price,
            "low": 99 + i * step_price, "close": 101 + i * step_price,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


def test_backtest_pnl_converges_on_live_statutory_costs():
    """Same strategy/data: the statutory-charged backtest is exactly the
    zero-cost backtest minus the statutory total — the gap that made simulated
    PnL diverge from live is now modelled."""
    from ntrade.backtest.simulator import BacktestSimulator

    zero = BacktestSimulator(timeframe="5m", initial_cash=100_000.0, statutory=None)
    zero.register_strategy(BuySellOnCandles())
    r_zero = zero.run(_ohlcv())

    real = BacktestSimulator(timeframe="5m", initial_cash=100_000.0)
    real.register_strategy(BuySellOnCandles())
    r_real = real.run(_ohlcv())

    assert r_real.n_trades == r_zero.n_trades == 2
    assert r_zero.statutory_total == 0.0
    assert r_real.statutory_total > 0.0
    # identical fills → equity differs only by the statutory charges
    assert r_real.final_equity == pytest.approx(r_zero.final_equity - r_real.statutory_total)
    assert r_real.costs_total == pytest.approx(r_real.commissions_total + r_real.statutory_total)
    # per-fill statutory matches the model on the actual fill notionals
    model = IndianStatutoryCosts()
    buy = r_real.trades[0]
    assert buy["statutory"] == pytest.approx(
        round(model.total_cost(buy["fill_price"] * buy["quantity"], "BUY"), 4))


def test_kernel_default_target_carries_statutory_model():
    k = _kernel()
    targets = list(k.router._targets.values())
    assert len(targets) == 1
    assert isinstance(targets[0].statutory, IndianStatutoryCosts)
