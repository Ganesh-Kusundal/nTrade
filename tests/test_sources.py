"""MarketFeedSource + SimulatedFeedSource tests (Slice E1)."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.sources.market_feed import MarketFeedSource, SimulatedFeedSource


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("SIM"))
    return k


def test_simulated_source_requires_input():
    with pytest.raises(ValueError):
        SimulatedFeedSource()


def test_simulated_prices_feed_projects_quotes():
    k = _kernel()
    src = SimulatedFeedSource(k, symbol="SIM", prices=[100.0, 101.0, 102.0])
    src.start()
    assert k.ctx.instrument("SIM").market.ltp() == 102.0
    assert src.ticks_published == 3
    assert len([e for e in k.bus.history if isinstance(e, TickEvent)]) == 3


def test_simulated_frame_feed_builds_candles():
    k = _kernel()
    start = datetime(2026, 1, 1, 9, 15)
    rows = [{
        "timestamp": start + timedelta(minutes=i),
        "open": 100.0 + i, "high": 102.0 + i,
        "low": 99.0 + i, "close": 101.0 + i, "volume": 1000,
    } for i in range(30)]
    src = SimulatedFeedSource(k, symbol="SIM", data=pd.DataFrame(rows))
    src.start()
    assert k.ctx.instrument("SIM").market.ltp() == 130.0  # last close
    assert src.ticks_published == 30
    # 29 buckets closed by the 30th tick
    assert len(k.candle_engine.candles("SIM")) == 29


def test_source_attach_and_stop():
    src = SimulatedFeedSource(prices=[1.0])
    k = _kernel()
    src.attach(k)
    src.start()
    assert k.ctx.instrument("SIM").market.ltp() == 1.0
    src.stop()  # no-op, must not raise


def test_source_is_abc():
    with pytest.raises(TypeError):
        MarketFeedSource()
