"""Integration test — full pipeline: feed → kernel → strategy → risk → execution → portfolio."""

from datetime import datetime

import pandas as pd
import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategy_engine import Strategy
from ntrade.events.risk import SignalApprovedEvent, SignalGeneratedEvent
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.session import TradingKernel


class _BuyOnFirstCandle(Strategy):
    """Fire a single BUY market order on the first candle close."""

    name = "buy_first_candle"

    def __init__(self):
        self._fired = False

    def on_candle_closed(self, event):
        if self._fired:
            return
        self._fired = True
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange,
            side="BUY", quantity=10,
        )


def _ohlcv_1m(n, start_price=100.0, step=1.0, spread=2.0, volume=1000, freq="1min"):
    """n rows of 1-minute OHLCV data — a gentle uptrend."""
    closes = [start_price + i * step for i in range(n)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq=freq),
        "open": closes,
        "high": [c + spread for c in closes],
        "low": [c - spread for c in closes],
        "close": closes,
        "volume": [volume] * n,
    })


def test_full_pipeline_end_to_end():
    """Feed -> kernel -> strategy -> risk -> execution -> portfolio — all engines fire."""
    from ntrade.brokers.paper import PaperBroker
    from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource

    broker = PaperBroker()
    kernel = TradingKernel(
        mode="live", clock=LiveClock(), timeframe="5m",
        broker=broker, initial_cash=1_000_000.0,
    )
    instrument = Equity("RELIANCE", broker=broker)
    kernel.register(instrument)

    strategy = _BuyOnFirstCandle()
    kernel.register_strategy(strategy)

    # 15 rows of 1m data -> ticks span 3 different 5-min buckets -> 2 closed candles
    frame = _ohlcv_1m(15)
    source = SyntheticMarketFeedSource(
        kernel, symbol="RELIANCE", exchange="NSE", data=frame,
    )
    source.attach(kernel)
    kernel.start()
    source.start()

    # Wait for the background thread to finish publishing all ticks
    source.join(timeout=30)

    # 1. MarketEngine processed ticks -> instrument has a quote
    assert instrument._quote.ltp > 0, "MarketEngine should have updated the quote"

    # 2. CandleEngine built candles (flush closes the last open candle)
    kernel.candle_engine.flush()
    candles = kernel.candle_engine.candles("RELIANCE")
    assert len(candles) >= 1, "CandleEngine should have closed at least one candle"

    # 3. Strategy fired a signal
    bus_history = kernel.bus.history
    signal_events = [e for e in bus_history if isinstance(e, SignalGeneratedEvent)]
    assert len(signal_events) >= 1, "Strategy should have emitted at least one signal"

    # 4. Risk approved the signal (no risk breakers configured -> signal passes)
    approved = [e for e in bus_history if isinstance(e, SignalApprovedEvent)]
    assert len(approved) >= 1, "RiskEngine should have approved the signal"

    # 5. Portfolio updated — balance decreased from the initial cash
    assert kernel.balance > 0, "Balance should be positive"
    assert kernel.balance < 1_000_000.0, "Balance should have decreased after BUY fill"

    kernel.stop(reason="integration test done")


def test_full_pipeline_multiple_candles():
    """Multiple candles produce multiple strategy evaluations."""
    from ntrade.brokers.paper import PaperBroker
    from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource

    broker = PaperBroker()
    kernel = TradingKernel(
        mode="live", clock=LiveClock(), timeframe="5m",
        broker=broker, initial_cash=500_000.0,
    )
    instrument = Equity("TCS", broker=broker)
    kernel.register(instrument)

    strategy = _BuyOnFirstCandle()
    kernel.register_strategy(strategy)

    # 25 rows of 1m data -> ticks span 5 different 5-min buckets -> 4 closed candles
    frame = _ohlcv_1m(25, start_price=2000.0, step=10.0, spread=5.0, volume=5000)
    source = SyntheticMarketFeedSource(
        kernel, symbol="TCS", exchange="NSE", data=frame,
    )
    source.attach(kernel)
    kernel.start()
    source.start()

    # Wait for the background thread to finish
    source.join(timeout=60)

    # Flush any open candle so we can count all closed candles
    kernel.candle_engine.flush()
    candles = kernel.candle_engine.candles("TCS")
    assert len(candles) >= 5, f"Expected >=5 candles, got {len(candles)}"

    # Strategy only fires once (on the first candle close)
    signals = [e for e in kernel.bus.history if isinstance(e, SignalGeneratedEvent)]
    assert len(signals) == 1

    kernel.stop(reason="multi-candle test done")
