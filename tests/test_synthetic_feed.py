"""SyntheticMarketFeedSource: 1m OHLCV frame -> 1-second ticks (G2-A2).

The key invariant: a 1m CandleEngine fed only these ticks reconstructs the
source bars exactly (open/high/low/close/volume) — high and low respected.
"""
from datetime import datetime, timedelta

import pandas as pd

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import CandleClosedEvent, DepthEvent, TickEvent
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


# ------------------------------------------------------------ simulated depth

def test_no_depth_events_by_default():
    """Zero-parity: depth simulation is opt-in — the default feed stays depth-free."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("SYM"))
    src = SyntheticMarketFeedSource(k, symbol="SYM", exchange="NSE", data=_frame(2))
    src.start()
    src.join(timeout=5)
    assert src.depth_events_published == 0
    assert not [e for e in k.bus.history if isinstance(e, DepthEvent)]


def test_depth_events_published_when_enabled():
    """With depth_levels>0 one DepthEvent per bar is published into the book."""
    frame = _frame(2)
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("SYM"))
    src = SyntheticMarketFeedSource(
        k, symbol="SYM", exchange="NSE", data=frame,
        depth_levels=5, depth_imbalance=0.0, depth_seed=42)
    src.start()
    src.join(timeout=5)
    k.candle_engine.flush()
    assert src.depth_events_published == 2
    depth_events = [e for e in k.bus.history if isinstance(e, DepthEvent)]
    assert len(depth_events) == 2
    # Book lands in the instrument read-model with tick-aligned prices
    inst = k.ctx.instrument("SYM")
    book = inst.market.depth()
    assert len(book.bids) == 5 and len(book.asks) == 5
    assert book.best_bid().price < book.best_ask().price


def test_depth_imbalance_reaches_read_model():
    """The requested imbalance is what the strategy's filter would see."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("SYM"))
    src = SyntheticMarketFeedSource(
        k, symbol="SYM", exchange="NSE", data=_frame(1),
        depth_levels=5, depth_imbalance=0.6, depth_seed=7)
    src.start()
    src.join(timeout=5)
    inst = k.ctx.instrument("SYM")
    assert inst.market.depth().bid_ask_imbalance() > 0.5


def test_depth_bar_mode_derives_pressure_from_bar_move():
    """Bar mode: each bar's book follows its own move (up -> buy, down -> sell)."""
    rows = [
        {"timestamp": datetime(2026, 7, 30, 9, 15), "open": 100.0, "high": 104.0,
         "low": 99.0, "close": 103.0, "volume": 600},   # up bar
        {"timestamp": datetime(2026, 7, 30, 9, 16), "open": 103.0, "high": 104.0,
         "low": 98.0, "close": 99.0, "volume": 600},   # down bar
    ]
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("SYM"))
    src = SyntheticMarketFeedSource(
        k, symbol="SYM", exchange="NSE", data=pd.DataFrame(rows),
        depth_levels=5, depth_imbalance=0.5, depth_imbalance_mode="bar", depth_seed=7)
    src.start()
    src.join(timeout=5)
    events = [e for e in k.bus.history if isinstance(e, DepthEvent)]
    assert len(events) == 2
    book_up = k.ctx.instrument("SYM").market.depth()   # last bar is the down bar
    assert book_up.bid_ask_imbalance() < 0
