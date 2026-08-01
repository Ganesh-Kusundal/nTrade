"""SyntheticMarketFeedSource: 1m OHLCV frame -> 1-second ticks (G2-A2).

The key invariant: a 1m CandleEngine fed only these ticks reconstructs the
source bars exactly (open/high/low/close/volume) — high and low respected.
"""
from datetime import datetime, timedelta

import pandas as pd

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import CandleClosedEvent, TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


def _frame(n: int = 5, start: float = 100.0):
    rows = []
    for i in range(n):
        rows.append({
            "timestamp": datetime(2026, 7, 30, 9, 15) + timedelta(minutes=i),
            "open": start + i, "high": start + i + 4, "low": start + i - 3,
            "close": start + i + 1, "volume": 600,
        })
    return pd.DataFrame(rows)


def _run(frame):
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("SYM"))
    src = SyntheticMarketFeedSource(k, symbol="SYM", exchange="NSE", data=frame)
    src.start()
    src.join(timeout=5)
    k.candle_engine.flush()
    return k, src


def test_publishes_60_ticks_per_bar():
    k, src = _run(_frame(2))
    assert src.ticks_published == 120
    assert len([e for e in k.bus.history if isinstance(e, TickEvent)]) == 120


def test_reconstructs_bars_exactly():
    frame = _frame(3)
    k, _ = _run(frame)
    candles = k.candle_engine.candles("SYM")
    assert len(candles) == 3
    for bar, c in zip(frame.to_dict("records"), candles):
        assert isinstance(c, CandleClosedEvent)
        assert c.open == bar["open"]
        assert c.high == bar["high"]
        assert c.low == bar["low"]
        assert c.close == bar["close"]
        assert c.volume == bar["volume"]


def test_instrument_quote_reflects_last_bar():
    frame = _frame(2)
    k, _ = _run(frame)
    inst = k.ctx.instrument("SYM")
    assert inst.market.ltp() == frame["close"].iloc[-1]
