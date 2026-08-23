"""Strategy registry factory — one registration point loads every mode."""
from ntrade.engines.strategies import HalfTrendStrategy
from ntrade.registry import strategy as strategy_reg


def test_strategy_instantiate_halftrend():
    inst = strategy_reg.instantiate("halftrend", symbol="NIFTY", exchange="NSE", lot_size=1)
    assert isinstance(inst, HalfTrendStrategy)
    assert inst.symbol == "NIFTY"
    assert inst.lot_size == 1


def test_strategy_registry_lists_orb_vwap():
    assert "orb_vwap" in strategy_reg.registry
    spec = strategy_reg.get("orb_vwap")
    assert spec.factory is not None
    assert "vwap" in spec.indicators


def test_load_strategy_via_backtest_simulator():
    from ntrade.backtest.simulator import BacktestSimulator

    sim = BacktestSimulator(timeframe="1m", initial_cash=100_000.0, statutory=None)
    sim.load_strategy("halftrend", symbol="NIFTY", exchange="NSE", lot_size=1)
    assert len(sim.kernel.strategy_engine.strategies) == 1
    assert sim.kernel.strategy_engine.strategies[0].name == "halftrend"
