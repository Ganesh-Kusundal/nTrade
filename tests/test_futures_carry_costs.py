"""Futures holding-period costs (feature 2) — carry drag + expiry roll slippage.

A futures backtest that holds across bars and into expiry pays the carry drag
(contango) and the roll spread live; modelling both makes simulated PnL
converge on live roll behavior. Costs are opt-in via
``BacktestSimulator(futures_costs=FuturesCarryCosts())``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from ntrade.backtest.simulator import BacktestSimulator
from ntrade.domain.instruments.derivatives import Future
from ntrade.engines.strategy_engine import Strategy
from ntrade.execution.costs import FuturesCarryCosts


def test_daily_carry_and_roll_cost_math():
    costs = FuturesCarryCosts(risk_free=0.065, dividend_yield=0.0, roll_pct=0.0002)
    assert costs.daily_carry(1_000_000.0, 365) == pytest.approx(65_000.0)
    assert costs.daily_carry(1_000_000.0, 1) == pytest.approx(1_000_000.0 * 0.065 / 365.0)
    assert costs.daily_carry(1_000_000.0, 0) == 0.0
    assert costs.roll_cost(1_000_000.0) == pytest.approx(200.0)
    assert costs.within_window(date(2026, 1, 30), date(2026, 1, 28))    # 2 days out
    assert not costs.within_window(date(2026, 1, 30), date(2026, 1, 10))  # too early
    assert not costs.within_window(date(2026, 1, 30), date(2026, 1, 31))  # past expiry
    assert not costs.within_window(None, date(2026, 1, 28))


class BuyAndHoldFuture(Strategy):
    name = "hold_future"

    def __init__(self, quantity: int = 75):
        super().__init__()
        self.quantity = quantity
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=event.price)
            self.done = True


def _daily_ohlcv(days: int = 10, start: date = date(2026, 1, 1)) -> pd.DataFrame:
    rows = []
    for i in range(days):
        ts = datetime.combine(start + timedelta(days=i), datetime.min.time().replace(hour=9, minute=15))
        rows.append({
            "timestamp": ts, "open": 100.0 + i, "high": 101.0 + i,
            "low": 99.0 + i, "close": 100.5 + i, "volume": 1000,
        })
    return pd.DataFrame(rows)


def test_futures_backtest_charges_carry_and_roll():
    expiry = date(2026, 1, 6)
    data = _daily_ohlcv(start=date(2026, 1, 1))  # day1..day10, expiry on day6
    costs = FuturesCarryCosts(risk_free=0.065, dividend_yield=0.0, roll_pct=0.0002)
    fut = Future("NIFTY JAN FUT", exchange="NFO", underlying="NIFTY", expiry=expiry)

    def run(futures_costs):
        sim = BacktestSimulator(symbol="NIFTY JAN FUT", exchange="NFO",
                                initial_cash=1_000_000.0, instrument=fut,
                                statutory=None, futures_costs=futures_costs)
        sim.register_strategy(BuyAndHoldFuture())
        return sim.run(data)

    r = run(costs)
    r0 = run(None)

    assert r0.futures_costs_total == 0.0
    assert r.futures_costs_total > 0.0
    # carry accrues on day2..day6 (5 sessions inside the carry window) + one roll
    entry_notional = r.trades[0]["fill_price"] * 75
    carry_days = (expiry - date(2026, 1, 1)).days
    expected = carry_days * costs.daily_carry(entry_notional, 1) + costs.roll_cost(entry_notional)
    assert r.futures_costs_total == pytest.approx(expected, abs=0.01)
    # convergence: identical fills, equity differs only by the futures costs
    assert r.final_equity == pytest.approx(r0.final_equity - r.futures_costs_total, abs=0.02)
    assert r.costs_total == pytest.approx(r.futures_costs_total, abs=0.01)


def test_futures_costs_only_within_carry_window():
    """A position closed before the window accrues nothing (no double-count)."""
    expiry = date(2026, 1, 30)
    data = _daily_ohlcv(days=5, start=date(2026, 1, 1))  # all before the window
    costs = FuturesCarryCosts(carry_window_days=5)
    fut = Future("NIFTY JAN FUT", exchange="NFO", underlying="NIFTY", expiry=expiry)

    sim = BacktestSimulator(symbol="NIFTY JAN FUT", exchange="NFO",
                            initial_cash=1_000_000.0, instrument=fut,
                            statutory=None, futures_costs=costs)
    sim.register_strategy(BuyAndHoldFuture())
    r = sim.run(data)
    # expiry is 25 days out — outside the 5-day window → no carry, no roll
    assert r.futures_costs_total == 0.0
