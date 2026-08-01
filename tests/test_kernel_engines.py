"""MarketEngine, CandleEngine and IndicatorEngine tests (kernel Slice B)."""

from datetime import datetime, timedelta

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import (
    CandleClosedEvent, DepthEvent, IndicatorUpdatedEvent, QuoteEvent,
    QuoteUpdatedEvent, TickEvent,
)
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("RELIANCE"))
    return k


def _tick(price: float, minute: int = 0):
    return TickEvent(symbol="RELIANCE", exchange="NSE", price=price, quantity=100,
                     ts=datetime(2026, 1, 1, 9, 15) + timedelta(minutes=minute))


def test_market_engine_projects_tick():
    k = _kernel()
    updated = []
    k.bus.subscribe(QuoteUpdatedEvent, lambda e: updated.append(e))
    k.bus.publish(_tick(2500.5))
    inst = k.ctx.instrument("RELIANCE")
    assert inst.market.ltp() == 2500.5
    assert inst.stream.last_tick is not None and inst.stream.last_tick.price == 2500.5
    assert updated and updated[0].ltp == 2500.5


def test_market_engine_broadcasts_tick_own_price_for_depth_kind():
    k = _kernel()
    updated = []
    k.bus.subscribe(QuoteUpdatedEvent, lambda e: updated.append(e))
    tick = TickEvent(symbol="RELIANCE", exchange="NSE", price=2498.75, quantity=0,
                     kind="depth", ts=datetime(2026, 1, 1, 9, 15))
    k.bus.publish(tick)
    assert updated and updated[0].ltp == tick.price


def test_market_engine_projects_quote():
    k = _kernel()
    k.bus.publish(QuoteEvent(symbol="RELIANCE", exchange="NSE", ltp=2500.0, bid=2499.5,
                             ask=2500.5, open=2490.0, high=2510.0, low=2485.0,
                             prev_close=2480.0, volume=1000, oi=500,
                             ts=datetime(2026, 1, 1, 9, 15)))
    inst = k.ctx.instrument("RELIANCE")
    assert inst.market.ltp() == 2500.0 and inst.market.bid() == 2499.5 and inst.market.ask() == 2500.5
    assert inst.market.volume() == 1000 and inst.market.oi() == 500


def test_market_engine_projects_depth():
    k = _kernel()
    k.bus.publish(DepthEvent(symbol="RELIANCE", exchange="NSE",
                             bids=((2499.5, 100, 3), (2499.0, 200, 4)),
                             asks=((2500.5, 90, 2),),
                             ts=datetime(2026, 1, 1, 9, 15)))
    depth = k.ctx.instrument("RELIANCE").market.depth()
    assert depth.best_bid().price == 2499.5
    assert depth.best_ask().quantity == 90


def test_candle_engine_closes_candle_on_next_bucket():
    k = _kernel()
    closed = []
    k.bus.subscribe(CandleClosedEvent, lambda e: closed.append(e))
    k.bus.publish(_tick(100.0, minute=0))
    k.bus.publish(_tick(102.0, minute=0))  # same bucket
    k.bus.publish(_tick(99.0, minute=1))   # next bucket closes previous
    assert len(closed) == 1
    candle = closed[0]
    assert candle.timeframe == "1m"
    assert candle.open == 100.0 and candle.high == 102.0
    assert candle.low == 100.0 and candle.close == 102.0  # 99 is in the next bucket
    assert candle.volume == 200  # two ticks × 100 qty


def test_candle_engine_flush_closes_partial():
    k = _kernel()
    closed = []
    k.bus.subscribe(CandleClosedEvent, lambda e: closed.append(e))
    k.bus.publish(_tick(100.0, minute=0))
    k.candle_engine.flush()
    assert len(closed) == 1


def test_candle_engine_rejects_bad_timeframe():
    with pytest.raises(ValueError):
        TradingKernel(timeframe="7x")


def test_indicator_engine_publishes_bundle_after_enough_candles():
    k = _kernel()
    bundles = []
    k.bus.subscribe(IndicatorUpdatedEvent, lambda e: bundles.append(e))
    for minute in range(40):
        price = 100.0 + minute * 0.1
        k.bus.publish(_tick(price, minute=minute))
    assert len(bundles) >= 1
    latest = bundles[-1].indicators
    assert "rsi_14" in latest
    assert "vwap" in latest
    # instrument read model updated by the engine
    assert "rsi_14" in k.ctx.instrument("RELIANCE").analytics.indicators
