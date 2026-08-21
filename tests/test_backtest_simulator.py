"""Tests for the BacktestSimulator (ntrade.backtest.simulator)."""

import pytest


@pytest.mark.timeout(60)
def test_results_fills_not_truncated_by_bus_cap():
    """results() must use a dedicated unbounded fill list, not the 10k-event
    bus history cap (regression: long runs silently dropped early fills).

    Minimal deterministic strategy (no indicators) so the regression is pinned
    without pandas indicator hot paths.
    """
    import pandas as pd
    from datetime import datetime, timedelta
    from ntrade.backtest.simulator import BacktestSimulator
    from ntrade.engines.strategy_engine import Strategy
    from ntrade.execution.costs import FixedSlippage, FlatCommission
    from ntrade.events.order import OrderFilledEvent

    class OneWay(Strategy):
        name = "oneway"
        def __init__(self):
            super().__init__()
            self.count = 0

        def on_candle_closed(self, event):
            # BUY on the 11th closed candle, sell on the 21st — deterministic,
            # no TA. (Bar-count, not ts-match: candle labels are naive UTC
            # while row timestamps are naive IST wall clock.)
            self.count += 1
            if self.count == 11:
                self.emit_signal(symbol=event.symbol, exchange=event.exchange, side="BUY", quantity=1, price=event.close, order_type="LIMIT")
            elif self.count == 21:
                self.emit_signal(symbol=event.symbol, exchange=event.exchange, side="SELL", quantity=1, price=event.close, order_type="LIMIT")

    rows = []
    t0 = datetime(2026, 7, 27, 9, 15)
    px = 100.0
    for i in range(30):
        ts = t0 + timedelta(minutes=i)
        rows.append({"timestamp": ts, "open": px, "high": px + 0.4, "low": px - 0.4, "close": px + 0.1, "volume": 1000.0})
    df = pd.DataFrame(rows)
    t10 = df.loc[10, "timestamp"]
    t20 = df.loc[20, "timestamp"]

    sim = BacktestSimulator(
        symbol="NIFTY", exchange="NFO", timeframe="1m", initial_cash=1_000_000.0,
        slippage=FixedSlippage(0.05), commission=FlatCommission(20.0), statutory=None,
    )
    sim.register_strategy(OneWay())
    # Pre-flood the bus cap so results() must not rely on bus.history
    from ntrade.events.base import Event
    from dataclasses import dataclass
    @dataclass(frozen=True, kw_only=True)
    class _Dummy(Event):
        pass
    for _ in range(10050):
        sim.kernel.bus.publish(_Dummy(ts=t0))
    sim.run(df)

    # The dedicated fill list must derive results, not the capped bus.
    assert hasattr(sim, "_fills"), "simulator must expose a dedicated _fills list"
    bus_fills = [e for e in sim.kernel.bus.history if isinstance(e, OrderFilledEvent)]
    res = sim.results()
    assert len(sim._fills) >= 1, "expected at least one fill"
    assert len(sim._fills) == len(res.trades), \
        f"results().trades ({len(res.trades)}) must equal _fills ({len(sim._fills)})"
    assert len(sim._fills) >= len(bus_fills), \
        "_fills must hold >= the bus's (capped) fill count"
