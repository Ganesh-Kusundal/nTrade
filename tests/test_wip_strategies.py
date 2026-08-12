"""Smoke tests for the WIP strategies (GainzClone + VwapReclaim)."""
import pandas as pd
from datetime import datetime, timedelta
import pytest

from ntrade.backtest.simulator import BacktestSimulator
from ntrade.engines.strategies import GainzCloneStrategy, VwapReclaimStrategy
from ntrade.execution.costs import FixedSlippage, FlatCommission


@pytest.mark.parametrize("strat_cls", [GainzCloneStrategy, VwapReclaimStrategy])
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
    res = sim.run(df)  # must not raise (NameError / TypeError before the fix)
    assert res is not None
    # The strategy engine SWALLOWS hook exceptions (logs + _error_count), so a
    # broken strategy still "runs" — assert it genuinely processed every bar
    # with zero swallowed errors. Pre-fix: GainzClone's `.time` TypeError fires
    # before the append (bars stay 0) and the missing hma/rsi import raises a
    # NameError past warmup (error counter climbs).
    assert len(strat._bars) == len(df), \
        f"strategy must process every bar, got {len(strat._bars)}/{len(df)}"
    assert getattr(strat, "_error_count", 0) == 0, \
        f"strategy swallowed {getattr(strat, '_error_count', 0)} handler errors"
