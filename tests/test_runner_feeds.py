"""synth/live feed factory (G2-B2)."""
from datetime import datetime

import pandas as pd
import pytest

from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.feeds import build_source
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


def _frame():
    return pd.DataFrame([{
        "timestamp": datetime(2026, 7, 30, 9, 15),
        "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0, "volume": 100,
    }])


def test_synth_builds_synthetic_source():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    src = build_source(k, feed="synth", symbol="NIFTY", frame=_frame())
    assert isinstance(src, SyntheticMarketFeedSource)


def test_synth_requires_frame():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    with pytest.raises(ValueError):
        build_source(k, feed="synth")


def test_live_builds_dhan_source():
    from ntrade.sources.dhan_feed import DhanMarketFeedSource
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m")
    src = build_source(k, feed="live", live_kwargs={"symbols": [(1, 2885)]})
    assert isinstance(src, DhanMarketFeedSource)


def test_unknown_feed_raises():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    with pytest.raises(ValueError):
        build_source(k, feed="bogus")
