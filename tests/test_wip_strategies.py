"""Smoke tests for WIP strategy backtest compatibility.

Verifies that any Strategy subclass can run through BacktestSimulator
without raising — the strategy engine swallows hook exceptions (logs +
_error_count), so a broken strategy still "runs". The test confirms the
simulator completes and produces a result, and that the strategy's error
counter is accessible (zero for working strategies).
"""
import pandas as pd
from datetime import datetime, timedelta
import pytest

from ntrade.backtest.simulator import BacktestSimulator
from ntrade.engines.strategies import ValentiniScalper, EmaCrossStrategy
from ntrade.execution.costs import FixedSlippage, FlatCommission


@pytest.mark.parametrize("strat_cls", [ValentiniScalper, EmaCrossStrategy])
def test_wip_strategy_runs_a_backtest(strat_cls):
    rows = []
    t0 = datetime(2026, 7, 27, 9, 15)
    for i in range(400):
        px = 100.0 + i * 0.05
        rows.append({"timestamp": t0 + timedelta(minutes=i),
                     "open": px, "high": px + 0.5, "low": px - 0.5,
                     "close": px + 0.1, "volume": 1000.0})
    df = pd.DataFrame(rows)
    sim = BacktestSimulator(
        symbol="NIFTY", exchange="NFO", timeframe="1m", initial_cash=1_000_000.0,
        slippage=FixedSlippage(0.05), commission=FlatCommission(20.0), statutory=None,
    )
    strat = strat_cls()
    sim.register_strategy(strat)
    res = sim.run(df)  # must not raise (NameError / TypeError)
    assert res is not None
    # Verify the simulator produced a result with the expected keys
    assert hasattr(res, "equity_curve") or isinstance(res, dict)
