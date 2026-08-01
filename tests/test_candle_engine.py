"""CandleEngine backtest-ohlcv tests (Task Group 2, B-007 / F-005).

Backtest bars carry real OHLCV via QuoteEvent; the paired close tick must not
double-count volume. CandleEngine must ignore live/replay QuoteEvents (they
carry day-session OHLCV, not bar shapes).
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from ntrade.events.market import QuoteEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _ohlcv(bars: int = 20, step_price: float = 1.0) -> pd.DataFrame:
    start = datetime(2026, 1, 1, 9, 15)
    rows = []
    for i in range(bars):
        rows.append({
            "timestamp": start + timedelta(minutes=5 * i),
            "open": 100 + i * step_price, "high": 102 + i * step_price,
            "low": 99 + i * step_price, "close": 101 + i * step_price,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


def _backtest():
    from ntrade.backtest.simulator import BacktestSimulator
    return BacktestSimulator(timeframe="5m", initial_cash=100_000.0, statutory=None)


def test_backtest_candles_carry_real_ohlcv():
    sim = _backtest()
    sim.run(_ohlcv())
    candles = sim.kernel.candle_engine.candles("NIFTY")
    assert len(candles) == 20
    for i, candle in enumerate(candles):
        assert candle.open == pytest.approx(100 + i)
        assert candle.high == pytest.approx(102 + i)
        assert candle.low == pytest.approx(99 + i)
        assert candle.close == pytest.approx(101 + i)
        assert candle.volume == 1000
    # not degenerate (open=high=low=close)
    for candle in candles[1:]:
        assert candle.open != candle.close


def test_backtest_indicators_populated_with_real_candles():
    sim = _backtest()
    sim.run(_ohlcv())
    indicators = sim.kernel.ctx.instrument("NIFTY")._indicators
    assert "rsi_14" in indicators
    assert "atr_14" in indicators
    assert "stx_10_3" in indicators
    # real high-low range (3.0) must drive ATR near 3.0; degenerate candles
    # (open=high=low=close) collapse it to ~1.0 and fail this check
    assert indicators["atr_14"] == pytest.approx(3.0, abs=0.5)


def test_scanners_tolerate_real_indicator_bundles():
    """Scanners must not crash on real indicator bundles and must only emit
    numeric indicator_values.

    stx_10_3 from compute_bundle is the supertrend DIRECTION string
    ('up'/'down'), not a price level — BreakoutScanner must fall through to the
    high/low breakout path instead of doing a string > int comparison
    (final review round regression).
    """
    from ntrade.kernel.trading_session import TradingSession
    from ntrade.scanners.builtin import (
        BreakoutScanner, MomentumScanner, VolumeSpikeScanner,
    )

    sim = _backtest()
    # Append a bar that closes through the prior high so the high/low
    # breakout fallback actually fires and emits indicator_values.
    df = _ohlcv(bars=20)
    last = {
        "timestamp": df["timestamp"].iloc[-1] + timedelta(minutes=5),
        "open": 121.0, "high": 124.0, "low": 120.0, "close": 130.0, "volume": 1000,
    }
    sim.run(pd.concat([df, pd.DataFrame([last])], ignore_index=True))

    inst = sim.kernel.ctx.instrument("NIFTY")
    assert "stx_10_3" in inst._indicators
    assert isinstance(inst._indicators["stx_10_3"], str)
    assert inst._quote.ltp == 130.0

    session = TradingSession(kernel=sim.kernel, mode="backtest")
    for scanner in (BreakoutScanner(), VolumeSpikeScanner(), MomentumScanner()):
        results = scanner.scan(session)
        for r in results:
            assert all(isinstance(v, (int, float))
                       for v in r.indicator_values.values())

    breakout = BreakoutScanner().scan(session)
    assert breakout, "high/low fallback should fire above the range"
    assert all(isinstance(v, (int, float)) for v in breakout[0].indicator_values.values())
    assert "stx_10_3" not in breakout[0].indicator_values


def test_candle_engine_ignores_live_quote_events():
    """Live/replay QuoteEvents carry day-session OHLCV and must not mint bars."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="5m")
    k.bus.publish(QuoteEvent(
        symbol="NIFTY", exchange="NSE", ltp=101.0, open=100.0, high=102.0,
        low=99.0, volume=1000, oi=0,
        ts=datetime(2026, 1, 1, 9, 15),
    ))
    assert k.candle_engine.candles("NIFTY") == []
    k.candle_engine.flush()
    assert k.candle_engine.candles("NIFTY") == []
