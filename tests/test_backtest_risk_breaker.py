"""Risk breaker evaluation in backtest — Task 2.

Verifies that the RiskEngine's circuit breakers (daily-loss, drawdown,
price-deviation) are evaluated on every bar in BacktestSimulator, not just
when a SignalGeneratedEvent arrives. Without per-bar evaluation, a position
that loses money on a bar with no signal would silently skip the daily-loss
cap — a NO-GO for real-money deployment.
"""

from datetime import datetime, timedelta

import pandas as pd

from ntrade.backtest.simulator import BacktestSimulator
from ntrade.domain.instruments.derivatives import Future
from ntrade.events.order import OrderFilledEvent
from ntrade.events.risk import SignalRejectedEvent
from ntrade.engines.strategies import Strategy


class _BuyAndHold(Strategy):
    """Enters a long on the first candle and holds — no exit signals.

    This exercises the risk breaker on bars with no new signal."""
    name = "buy_hold"

    def __init__(self, quantity: int = 10_000):
        super().__init__()
        self._qty = quantity
        self.done = False

    def on_candle_closed(self, event):
        if not self.done:
            self.emit_signal(
                symbol=event.symbol, exchange=event.exchange,
                side="BUY", quantity=self._qty, price=event.close,
            )
            self.done = True


def _make_data(start_price=100.0):
    """30 uptrend bars, then 15 crash bars.

    The crash takes the price well below entry (~100) so a 10k-share long
    position is deeply underwater — enough to trip a 5k daily-loss cap."""
    rows = []
    start = datetime(2026, 1, 1, 9, 15)
    for i in range(30):
        rows.append({
            "timestamp": start + timedelta(minutes=5 * i),
            "open": start_price + i,
            "high": start_price + i + 1,
            "low": start_price + i - 1,
            "close": start_price + i + 0.5,
            "volume": 1000,
        })
    # Crash: from ~130 down to ~30 (well below entry of ~100)
    for i in range(15):
        crash_close = 130 - i * 7.0
        rows.append({
            "timestamp": start + timedelta(minutes=5 * (30 + i)),
            "open": crash_close + 7,
            "high": crash_close + 7,
            "low": crash_close - 3,
            "close": crash_close,
            "volume": 2000,
        })
    return pd.DataFrame(rows)


def test_backtest_risk_breaker_trips_without_signal():
    """The daily-loss cap must trip in backtest even when no signal fires.

    _BuyAndHold opens a position on bar 2 and holds through the crash.
    With max_daily_loss=5000, the ~1600 loss should trip the breaker
    on a crash bar — without this, the breaker is only evaluated on
    signals (stale state, NO-GO for live money).
    """
    data = _make_data()
    sim = BacktestSimulator(
        symbol="NIFTY FUT",
        exchange="NFO",
        timeframe="1m",
        initial_cash=1_000_000.0,
        instrument=Future("NIFTY FUT", exchange="NFO"),
        risk_kwargs={"max_daily_loss": 5_000.0, "max_drawdown_pct": 1.0},
    )
    sim.register_strategy(_BuyAndHold())
    sim.run(data)
    assert sim.kernel.risk_engine.halted, (
        "RiskEngine should have tripped on a crash bar without a signal"
    )


def test_backtest_no_halt_when_loss_under_cap():
    """Sanity: a small position with a loss under the cap does not trip."""
    data = _make_data()
    sim = BacktestSimulator(
        symbol="NIFTY FUT",
        exchange="NFO",
        timeframe="1m",
        initial_cash=1_000_000.0,
        instrument=Future("NIFTY FUT", exchange="NFO"),
        risk_kwargs={"max_daily_loss": 100_000.0, "max_drawdown_pct": 50.0},
    )
    sim.register_strategy(_BuyAndHold(quantity=100))  # small position
    sim.run(data)
    assert not sim.kernel.risk_engine.halted


def test_backtest_risk_engine_check_called_between_signals():
    """Verify check() is invoked in the per-bar loop, not only on signals.

    We assert this by checking that the breaker trips on a bar where the
    strategy does NOT emit a signal (the crash bars have no entry signal).
    """
    data = _make_data()
    sim = BacktestSimulator(
        symbol="NIFTY FUT",
        exchange="NFO",
        timeframe="1m",
        initial_cash=1_000_000.0,
        instrument=Future("NIFTY FUT", exchange="NFO"),
        risk_kwargs={"max_daily_loss": 5_000.0},
    )
    sim.register_strategy(_BuyAndHold())
    sim.run(data)

    # There was exactly 1 signal (the BUY on bar 2).
    signals = [e for e in sim.kernel.bus.history
               if type(e).__name__ == "SignalGeneratedEvent"]
    assert len(signals) == 1

    # The halt happened after that signal — on a later crash bar.
    halts = [e for e in sim.kernel.bus.history
             if type(e).__name__ == "RiskHaltedEvent"]
    assert len(halts) >= 1
    assert halts[0].ts > signals[0].ts
