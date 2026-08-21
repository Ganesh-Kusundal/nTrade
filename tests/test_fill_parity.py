"""The zero-parity contract, executable: the same bar stream through the
backtest executor and the paper executor produces identical fills.

Regression guard for the MARKET->LIMIT heuristic (order_engine): before the
fix, backtest filled at the signal bar's OPEN (look-ahead) while paper filled
at the bar CLOSE. One signal, two prices. This test fails if that ever
returns.
"""
from datetime import datetime, timedelta

import pandas as pd

from ntrade.backtest.simulator import BacktestSimulator
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
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


def _feed_paper(paper_bars):
    """Drive the same bars through the REAL paper path (api/paper_trader.py):
    TradingSession.paper -> session.stock (factory injects the PaperBroker
    into the instrument) -> one tick per bar close -> stop() flushes the
    tail candle. No stubs anywhere on this path."""
    session = TradingSession.paper(initial_cash=100_000.0, timeframe="1m",
                                   statutory=None, clock=ReplayClock())
    session.register(session.stock(_SYMBOL))
    strategy = FlipOnCandle()
    session.register_strategy(strategy, risk={
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


def test_backtest_and_paper_fill_identically():
    sim = BacktestSimulator(timeframe="1m", initial_cash=100_000.0, statutory=None)
    sim.register_strategy(FlipOnCandle())
    result = sim.run(_bars())
    backtest_fills = [(t["side"], t["quantity"], t["fill_price"]) for t in result.trades]

    paper_fills = _feed_paper(_bars())

    assert len(backtest_fills) == 2, backtest_fills
    assert backtest_fills == paper_fills, (
        f"PARITY BREAK: backtest {backtest_fills} != paper {paper_fills}")
