"""Three-way zero-parity contract: the same bar stream through backtest,
replay, and paper produces identical fills.

Extends ``test_fill_parity`` (backtest ↔ paper) with replay so all three
execution entry points share one bar feed and must agree on fill triples.
"""
from datetime import datetime, timedelta

import pandas as pd

from ntrade.backtest.simulator import BacktestSimulator
from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategy_engine import Strategy
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.kernel.trading_session import TradingSession

_SYMBOL = "NIFTY"


class FlipOnCandle(Strategy):
    """BUY on the 3rd closed candle, SELL on the 8th — MARKET orders."""
    name = "flip"

    def __init__(self):
        super().__init__()
        self.count = 0

    def on_candle_closed(self, event):
        self.count += 1
        if self.count == 3:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=10, price=event.close,
                             reference_price=event.close)
        elif self.count == 8:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=10, price=event.close,
                             reference_price=event.close)


def _bars(n=12, start=100.0):
    rows = []
    for i in range(n):
        close = start + i
        rows.append({"timestamp": datetime(2026, 8, 21, 9, 15) + timedelta(minutes=i),
                     "open": close - 0.5, "high": close + 1.0,
                     "low": close - 1.0, "close": close, "volume": 100})
    return pd.DataFrame(rows)


def _ticks_from_bars(paper_bars):
    events = []
    for _, row in paper_bars.iterrows():
        ts = row["timestamp"]
        events.append(TickEvent(
            symbol=_SYMBOL, exchange="NSE", price=float(row["close"]),
            quantity=int(row["volume"]), ts=ts,
        ))
    return events


def _feed_paper(paper_bars):
    session = TradingSession.paper(initial_cash=100_000.0, timeframe="1m",
                                   statutory=None, clock=ReplayClock())
    session.register(session.stock(_SYMBOL))
    session.register_strategy(FlipOnCandle(), risk={
        "max_quantity": 10_000,
        "max_notional": 300_000_000.0,
        "max_daily_loss": 50_000.0,
        "max_drawdown_pct": 5.0,
    })
    fills = []
    session.kernel.bus.subscribe(OrderFilledEvent, fills.append)
    session.start()
    for _, row in paper_bars.iterrows():
        ts = row["timestamp"]
        session.kernel.clock.set(ts)
        session.kernel.bus.publish(TickEvent(
            symbol=_SYMBOL, exchange="NSE", price=float(row["close"]),
            quantity=int(row["volume"]), ts=ts))
    session.stop(reason="parity")
    return [(f.side, f.quantity, f.fill_price) for f in fills]


def _feed_replay(paper_bars):
    kernel = TradingKernel(
        mode="replay", clock=ReplayClock(), timeframe="1m",
        initial_cash=100_000.0, statutory=None,
    )
    kernel.register(Equity(_SYMBOL))
    kernel.register_strategy(FlipOnCandle())
    fills = []
    kernel.bus.subscribe(OrderFilledEvent, fills.append)
    kernel.start()
    kernel.run_replay(_ticks_from_bars(paper_bars))
    kernel.stop(reason="parity")
    return [(f.side, f.quantity, f.fill_price) for f in fills]


def test_backtest_replay_and_paper_fill_identically():
    bars = _bars()
    sim = BacktestSimulator(timeframe="1m", initial_cash=100_000.0, statutory=None)
    sim.register_strategy(FlipOnCandle())
    backtest_fills = [(t["side"], t["quantity"], t["fill_price"])
                      for t in sim.run(bars).trades]

    paper_fills = _feed_paper(bars)
    replay_fills = _feed_replay(bars)

    assert len(backtest_fills) == 2, backtest_fills
    assert backtest_fills == paper_fills, (
        f"PARITY BREAK backtest vs paper: {backtest_fills} != {paper_fills}")
    assert backtest_fills == replay_fills, (
        f"PARITY BREAK backtest vs replay: {backtest_fills} != {replay_fills}")
