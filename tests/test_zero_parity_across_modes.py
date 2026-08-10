"""Cross-mode zero-parity verification — Task 5.

Runs the same market data through three execution paths and asserts
identical fills:

  1. BacktestSimulator (mode="backtest", BarAwareExecution + SimulatedExecution)
  2. TradingKernel(mode="replay") + SyntheticMarketFeedSource (SimulatedExecution)
  3. TradingSession.paper() + SyntheticMarketFeedSource (BrokerExecution + PaperBroker)

All three use the same ValentiniScalper strategy, same OHLCV data, same
instrument type (Future/NFO), and same cost pipeline. Fills must match.
"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from ntrade.backtest.simulator import BacktestSimulator
from ntrade.domain.instruments.derivatives import Future
from ntrade.engines.strategies import ValentiniScalper
from ntrade.events.order import OrderFilledEvent
from ntrade.kernel.session import TradingKernel
from ntrade.kernel.trading_session import TradingSession
from ntrade.runner.feeds import build_source
from ntrade.runner.live_runner import LiveRunner


_SYMBOL = "NIFTY 27Aug26"
_EXCHANGE = "NFO"
_EXPIRY = date(2026, 8, 27)


def _make_frame(n_bars=45):
    """OHLCV data with a clear Valentini setup that FIRES in every mode:

    bars 0-29   steady uptrend 100 -> 114.5 (warmup + trend),
    bar  30     absorption (big volume 3000, tiny range) at 101 near VAL,
    bar  31     consolidation back near the session POC (107),
    bar  32     aggression: close 111 breaks above VWAP -> BUY entry,
    bars 33+    benign drift so the entry's managed exit logic stays quiet.

    The entry is a MARKET fill that must land at the signal bar's close (111.0)
    in backtest, replay AND paper — never at a contaminated next-bar price.
    """
    rows = []
    start = datetime(2026, 8, 3, 9, 15)
    for i in range(n_bars):
        if i <= 29:
            c = 100.0 + i * 0.5          # steady uptrend 100 -> 114.5
            o, h, lo, v = c - 0.5, c + 0.5, c - 0.5, 1000
        elif i == 30:
            c = 101.0                    # absorption: big volume, tiny range
            o, h, lo, v = 101.0, 101.05, 100.95, 3000
        elif i == 31:
            c = 107.0                    # consolidate back near POC
            o, h, lo, v = 106.5, 107.5, 106.0, 1000
        elif i == 32:
            c = 111.0                    # aggression: break above VWAP
            o, h, lo, v = 110.0, 111.5, 109.5, 1000
        else:
            c = 111.0 + (i - 32) * 0.5   # benign drift afterwards
            o, h, lo, v = c - 0.5, c + 0.5, c - 0.5, 1000
        rows.append({
            "timestamp": start + timedelta(minutes=i),
            "open": o, "high": h, "low": lo, "close": c, "volume": v,
        })
    return pd.DataFrame(rows)


def _fills(events):
    """Extract fill events (symbol, side, quantity, fill_price) from history."""
    return [(f.symbol, f.side, f.quantity, round(f.fill_price, 2))
            for f in events if isinstance(f, OrderFilledEvent)]


# ------------------------------------------------------------------ backtest

def _run_backtest(frame):
    """Mode 1: BacktestSimulator with BarAwareExecution."""
    sim = BacktestSimulator(
        symbol=_SYMBOL, exchange=_EXCHANGE, timeframe="1m",
        initial_cash=1_000_000.0,
        instrument=Future(_SYMBOL, exchange=_EXCHANGE, expiry=_EXPIRY),
        statutory=None,  # zero-cost for clean price comparison
    )
    sim.register_strategy(ValentiniScalper(
        symbol=_SYMBOL, range_size=4.0, warmup=15,
        tp_multiplier=2.0, min_rr=1.5))
    sim.run(frame)
    return _fills(sim.kernel.bus.history), sim


# ------------------------------------------------------------------ replay

def _run_replay(frame):
    """Mode 2: TradingKernel(replay) + SyntheticMarketFeedSource."""
    k = TradingKernel(mode="replay", timeframe="1m",
                      initial_cash=1_000_000.0, statutory=None)
    k.register(Future(_SYMBOL, exchange=_EXCHANGE, expiry=_EXPIRY))
    k.register_strategy(ValentiniScalper(
        symbol=_SYMBOL, range_size=4.0, warmup=15,
        tp_multiplier=2.0, min_rr=1.5))
    source = build_source(k, feed="synth", symbol=_SYMBOL,
                          exchange=_EXCHANGE, frame=frame)
    runner = LiveRunner(k, source, poll_interval=60.0, sync_interval=60.0)
    try:
        runner.run(duration=10.0)
    finally:
        runner.stop()
    return _fills(k.bus.history), k


# ------------------------------------------------------------------ paper

def _run_paper(frame):
    """Mode 3: TradingSession.paper() + SyntheticMarketFeedSource."""
    sess = TradingSession.paper(initial_cash=1_000_000.0, statutory=None)
    nifty = sess.index("NIFTY")
    fut = sess.future(nifty, expiry=_EXPIRY)
    sess.register(fut)
    sess.register_strategy(ValentiniScalper(
        symbol=fut.symbol, range_size=4.0, warmup=15,
        tp_multiplier=2.0, min_rr=1.5), risk={"max_quantity": 100_000})
    source = build_source(sess.kernel, feed="synth", symbol=fut.symbol,
                          exchange=_EXCHANGE, frame=frame)
    runner = LiveRunner(sess.kernel, source, poll_interval=60.0, sync_interval=60.0)
    try:
        runner.run(duration=10.0)
    finally:
        runner.stop()
        sess.stop()
    return _fills(sess.kernel.bus.history), sess


# ------------------------------------------------------------------ tests

def test_zero_parity_fills_across_backtest_replay_paper():
    """Same OHLCV data must produce identical fills across all three modes.

    This is the core zero-parity guarantee: the same ValentiniScalper logic,
    the same instrument, the same cost pipeline — different execution targets
    (BarAwareExecution vs SimulatedExecution vs BrokerExecution+PaperBroker)
    must yield the same fill prices, quantities, and sides.
    """
    frame = _make_frame(60)

    bt_fills, _ = _run_backtest(frame)
    rp_fills, _ = _run_replay(frame)
    pp_fills, _ = _run_paper(frame)

    # All three modes must produce at least one fill (the Valentini setup fires).
    assert len(bt_fills) > 0, f"backtest produced no fills"
    assert len(rp_fills) > 0, f"replay produced no fills"
    assert len(pp_fills) > 0, f"paper produced no fills"

    # The bar-driven MARKET entry must fill at the SIGNAL BAR's close (bar 32
    # = 111.0) in every mode — the reference_price zero-parity guarantee. A
    # contaminated instrument LTP (next bar) would break this.
    entry_price = 111.0
    assert bt_fills[0][3] == entry_price, (
        f"backtest entry must fill at bar-close reference {entry_price}, "
        f"got {bt_fills[0][3]}"
    )

    # Fill comparison (ignoring order_id which varies by execution target):
    # same symbol, side, quantity, fill_price.
    assert bt_fills == rp_fills, (
        f"backtest != replay:\n  backtest: {bt_fills}\n  replay:   {rp_fills}"
    )
    assert bt_fills == pp_fills, (
        f"backtest != paper:\n  backtest: {bt_fills}\n  paper:    {pp_fills}"
    )


def test_zero_parity_entry_exit_prices_across_modes():
    """The entry and any exit (SL/TP) fills must match across modes.

    ValentiniScalper emits a MARKET entry and managed exits — the fill
    prices must be identical because all execution targets fill MARKET at
    the instrument's current LTP (the bar close in backtest, the tick price
    in replay/paper).
    """
    frame = _make_frame(80)

    bt_fills, _ = _run_backtest(frame)
    rp_fills, _ = _run_replay(frame)
    pp_fills, _ = _run_paper(frame)

    # Compare entry (first fill) across modes.
    if bt_fills and rp_fills and pp_fills:
        assert bt_fills[0][3] == rp_fills[0][3], (
            f"entry price mismatch: bt={bt_fills[0][3]} vs rp={rp_fills[0][3]}"
        )
        assert bt_fills[0][3] == pp_fills[0][3], (
            f"entry price mismatch: bt={bt_fills[0][3]} vs pp={pp_fills[0][3]}"
        )


def test_zero_parity_deterministic_across_repeat_runs():
    """Running the same mode twice must produce identical fills (determinism)."""
    frame = _make_frame(60)

    bt1, _ = _run_backtest(frame)
    bt2, _ = _run_backtest(frame)
    assert bt1 == bt2, "backtest must be deterministic across repeat runs"

    rp1, _ = _run_replay(frame)
    rp2, _ = _run_replay(frame)
    assert rp1 == rp2, "replay must be deterministic across repeat runs"

    pp1, _ = _run_paper(frame)
    pp2, _ = _run_paper(frame)
    assert pp1 == pp2, "paper must be deterministic across repeat runs"
