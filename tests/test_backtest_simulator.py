"""Tests for the BacktestSimulator (ntrade.backtest.simulator)."""


def test_results_fills_not_truncated_by_bus_cap():
    """results() must use a dedicated unbounded fill list, not the 10k-event
    bus history cap (regression: long runs silently dropped early fills)."""
    import pandas as pd
    from datetime import datetime, timedelta
    from ntrade.backtest.simulator import BacktestSimulator
    from ntrade.engines.strategies import ValentiniScalper
    from ntrade.execution.costs import FixedSlippage, FlatCommission
    from ntrade.events.order import OrderFilledEvent

    # ~2 days x 375 bars x ~5 events/bar > 10k bus events: early fills would
    # be dropped by the bus deque if results() read bus.history.
    rows = []
    t0 = datetime(2026, 7, 27, 9, 15)
    for d in range(2):
        for i in range(375):
            px = 100.0 + (i % 50) * 0.1 + d * 0.5
            rows.append({"timestamp": t0 + timedelta(days=d, minutes=i),
                         "open": px, "high": px + 0.5, "low": px - 0.5,
                         "close": px + 0.1, "volume": 1000.0})
    df = pd.DataFrame(rows)

    sim = BacktestSimulator(
        symbol="NIFTY", exchange="NFO", timeframe="1m", initial_cash=1_000_000.0,
        slippage=FixedSlippage(0.05), commission=FlatCommission(20.0), statutory=None,
    )
    sim.register_strategy(ValentiniScalper(symbol="NIFTY", range_size=4.0, warmup=15))
    sim.run(df)

    # The dedicated fill list must have grown (any fills at all); results()
    # must derive from it so n_trades == len(_fills).
    assert hasattr(sim, "_fills"), "simulator must expose a dedicated _fills list"
    bus_fills = [e for e in sim.kernel.bus.history if isinstance(e, OrderFilledEvent)]
    res = sim.results()
    assert len(sim._fills) == len(res.trades), \
        f"results().trades ({len(res.trades)}) must equal _fills ({len(sim._fills)})"
    assert len(sim._fills) >= len(bus_fills), \
        "_fills must hold >= the bus's (capped) fill count"
