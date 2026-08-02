"""EventStore, ReplayEngine, zero-parity replay and backtest tests (Slice D)."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.events.risk import SignalApprovedEvent, SignalGeneratedEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.replay.replay_engine import ReplayEngine
from ntrade.storage.event_store import EventStore


def _ts(minute: int = 0):
    return datetime(2026, 1, 1, 9, 15) + timedelta(minutes=minute)


# ------------------------------------------------------------------ EventStore
def test_event_store_append_and_chronological_replay():
    store = EventStore()
    store.append(TickEvent(ts=_ts(1), symbol="NIFTY", exchange="NSE", price=101.0))
    store.append(TickEvent(ts=_ts(0), symbol="NIFTY", exchange="NSE", price=100.0))
    assert len(store) == 2
    assert [e.price for e in store.replay()] == [100.0, 101.0]
    assert len(store.events(symbol="NIFTY")) == 2
    assert len(store.events(symbol="OTHER")) == 0


def test_event_store_jsonl_roundtrip(tmp_path):
    path = tmp_path / "events.jsonl"
    store = EventStore(path=str(path))
    store.append(TickEvent(ts=_ts(0), symbol="NIFTY", exchange="NSE", price=100.0))
    reloaded = EventStore(path=str(path))
    assert len(reloaded) == 1
    assert reloaded.events()[0].price == 100.0
    assert reloaded.events()[0].ts == _ts(0)


def test_event_store_nested_events_roundtrip(tmp_path):
    path = tmp_path / "nested.jsonl"
    store = EventStore(path=str(path))
    signal = SignalGeneratedEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                                  quantity=10, price=100.0, strategy="t", ts=_ts(0))
    store.append(SignalApprovedEvent(signal=signal, ts=_ts(0)))
    reloaded = EventStore(path=str(path))
    approved = reloaded.events()[0]
    assert isinstance(approved, SignalApprovedEvent)
    assert approved.signal.symbol == "NIFTY"
    assert approved.signal.quantity == 10


# ------------------------------------------------------------------ ReplayEngine
class BuyOnFirstTick(Strategy):
    name = "buy_first"

    def __init__(self):
        super().__init__()
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=5, price=100.0)
            self.done = True


def test_replay_engine_runs_from_event_store():
    store = EventStore()
    store.append(TickEvent(ts=_ts(0), symbol="NIFTY", exchange="NSE", price=100.0))
    store.append(TickEvent(ts=_ts(1), symbol="NIFTY", exchange="NSE", price=101.0))
    engine = ReplayEngine()
    engine.kernel.register(Equity("NIFTY"))
    engine.run(store.replay())
    assert engine.kernel.ctx.instrument("NIFTY").market.ltp() == 101.0
    assert engine.clock.now() == _ts(1)  # clock follows the events


def test_zero_parity_replay_and_live_identical_fills():
    ticks = [TickEvent(symbol="NIFTY", exchange="NSE", price=100.0 + i, ts=_ts(i))
             for i in range(10)]

    def run(kernel):
        kernel.register(Equity("NIFTY"))
        kernel.register_strategy(BuyOnFirstTick())
        kernel.run_replay(ticks)
        return [(e.order_id, e.fill_price, e.side)
                for e in kernel.bus.history if isinstance(e, OrderFilledEvent)]

    live = TradingKernel(mode="live", clock=ReplayClock())
    replay = TradingKernel(mode="replay", clock=ReplayClock())
    live_fills = run(live)
    replay_fills = run(replay)
    assert live_fills == replay_fills
    assert live_fills == [("SIM-000001", 100.0, "BUY")]


# ------------------------------------------------------------------ Backtest
class BuySellOnCandles(Strategy):
    name = "buy_sell"

    def __init__(self):
        super().__init__()
        self.count = 0

    def on_candle_closed(self, event):
        self.count += 1
        if self.count == 3:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=10, price=event.close)
        elif self.count == 8:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=10, price=event.close)


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


def test_backtest_simulator_produces_equity_curve():
    from ntrade.backtest.simulator import BacktestSimulator

    sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0, statutory=None)
    sim.register_strategy(BuySellOnCandles())
    result = sim.run(_ohlcv())

    assert result.n_trades == 2
    assert result.trades[0]["side"] == "BUY"
    assert result.trades[1]["side"] == "SELL"
    assert len(result.equity_curve) == 20
    # candle i closes during bar i+1: buy at candle-2 close (103), sell at
    # candle-7 close (108) → profit 10 * 5
    assert result.trades[0]["fill_price"] == 103.0
    assert result.trades[1]["fill_price"] == 108.0
    assert result.final_equity == pytest.approx(100_000.0 + 10 * 5.0)


def test_backtest_fill_policy():
    from ntrade.backtest.fills import FillPolicy
    from ntrade.events.order import OrderIntentEvent

    policy = FillPolicy(market_on="close")
    bar = {"open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0}

    buy = OrderIntentEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                           quantity=1, order_type="LIMIT", price=100.5, ts=_ts())
    sell = OrderIntentEvent(symbol="NIFTY", exchange="NSE", side="SELL",
                            quantity=1, order_type="LIMIT", price=105.5, ts=_ts())
    miss = OrderIntentEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                            quantity=1, order_type="LIMIT", price=98.0, ts=_ts())
    assert policy.limit_fill(buy, bar) == 100.0  # min(limit, open)
    assert policy.limit_fill(sell, bar) == 105.5  # max(limit, open)
    assert policy.limit_fill(miss, bar) is None  # low never touched 98


def test_backtest_commissions_and_drawdown():
    from ntrade.backtest.simulator import BacktestSimulator
    from ntrade.execution.costs import PercentageCommission

    sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0, statutory=None,
                            commission=PercentageCommission(pct=0.01))
    sim.register_strategy(BuySellOnCandles())
    result = sim.run(_ohlcv())
    # buy 10@103 + sell 10@108 → notional 1030 + 1080, 1% each
    assert result.commissions_total == pytest.approx(10.30 + 10.80)
    assert result.max_drawdown_pct >= 0.0
    assert result.n_trades == 2
    assert result.final_equity == pytest.approx(100_000.0 + 50.0 - result.commissions_total)


def test_backtest_limit_fills_bar_aware():
    """BarAwareExecution: a limit order only fills when a bar trades through it."""
    from ntrade.backtest.fills import FillPolicy
    from ntrade.backtest.simulator import BacktestSimulator

    class LimitBuy(Strategy):
        name = "limit_buy"

        def __init__(self, limit: float):
            super().__init__()
            self.limit = limit
            self.done = False

        def on_candle_closed(self, event):
            if not self.done:
                self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                                 side="BUY", quantity=10, price=self.limit)
                self.done = True

    # limit far below every low → never fills
    sim = BacktestSimulator(timeframe="5m", fill_policy=FillPolicy())
    sim.register_strategy(LimitBuy(limit=1.0))
    result = sim.run(_ohlcv())
    assert result.n_trades == 0

    # limit within the first bar's range → fills at min(limit, open)
    sim2 = BacktestSimulator(timeframe="5m", fill_policy=FillPolicy())
    sim2.register_strategy(LimitBuy(limit=100.0))
    result2 = sim2.run(_ohlcv())
    assert result2.n_trades == 1
    assert result2.trades[0]["fill_price"] == 100.0  # min(100, open 100)


def test_backtest_market_orders_ignore_policy():
    """MARKET orders are unaffected by the fill policy."""
    from ntrade.backtest.fills import FillPolicy
    from ntrade.backtest.simulator import BacktestSimulator

    class MarketBuy(Strategy):
        name = "market_buy"

        def __init__(self):
            super().__init__()
            self.done = False

        def on_candle_closed(self, event):
            if not self.done:
                self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                                 side="BUY", quantity=10)
                self.done = True

    sim = BacktestSimulator(timeframe="5m", fill_policy=FillPolicy())
    sim.register_strategy(MarketBuy())
    result = sim.run(_ohlcv())
    assert result.n_trades == 1
    # MARKET fills at the bar-1 close (102) when the first candle closes
    assert result.trades[0]["fill_price"] == pytest.approx(102.0)


def test_event_store_tolerates_torn_final_line(tmp_path):
    """A crash mid-append leaves a truncated final JSONL line; _load must
    skip it rather than fail — ResilientKernel recovery reads exactly when
    a crash happened."""
    import json

    path = tmp_path / "torn.jsonl"
    valid = json.dumps({"__type__": "TickEvent", "ts": "2026-01-01T09:15:00",
                        "symbol": "NIFTY", "exchange": "NSE", "price": 100.0})
    torn = '{"__type__": "TickEvent", "ts": "2026-01-01T09:16:00", "symbol": "NIFTY'  # cut mid-append
    path.write_text(valid + "\n" + torn)
    store = EventStore(path=str(path))
    assert len(store) == 1
    assert store.events()[0].price == 100.0


def test_backtest_limit_fills_bar_aware_by_default():
    """The default backtest execution is bar-aware: a LIMIT order far below
    every bar's low never fills (it used to fill at its limit price)."""
    from ntrade.backtest.simulator import BacktestSimulator

    class LimitBuy(Strategy):
        name = "limit_buy_default"

        def __init__(self, limit: float):
            super().__init__()
            self.limit = limit
            self.done = False

        def on_candle_closed(self, event):
            if not self.done:
                self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                                 side="BUY", quantity=10, price=self.limit)
                self.done = True

    # No explicit fill_policy — the default must still require trade-through.
    sim = BacktestSimulator(timeframe="5m")
    sim.register_strategy(LimitBuy(limit=1.0))  # far below every low
    result = sim.run(_ohlcv())
    assert result.n_trades == 0

    # A limit within the first bar's range still fills.
    sim2 = BacktestSimulator(timeframe="5m")
    sim2.register_strategy(LimitBuy(limit=100.0))
    result2 = sim2.run(_ohlcv())
    assert result2.n_trades == 1
    assert result2.trades[0]["fill_price"] == 100.0  # min(100, open 100)


def test_event_store_skips_unknown_event_types(tmp_path):
    """A JSONL file with an unknown __type__ must not crash _load."""
    import json
    path = tmp_path / "mixed.jsonl"
    known = {"__type__": "TickEvent", "ts": "2026-01-01T09:15:00",
             "symbol": "NIFTY", "exchange": "NSE", "price": 100.0}
    unknown = {"__type__": "RemovedEvent_v1", "ts": "2026-01-01T09:16:00"}
    path.write_text(json.dumps(known) + "\n" + json.dumps(unknown) + "\n")
    store = EventStore(path=str(path))
    assert len(store) == 1  # unknown silently skipped


def test_event_store_persistent_file_handle(tmp_path):
    import json
    path = tmp_path / "persist.jsonl"
    store = EventStore(path=str(path))
    from ntrade.events.market import TickEvent
    from datetime import datetime
    for i in range(10):
        store.append(TickEvent(ts=datetime(2026, 1, 1, 9, i, 0), symbol="X", exchange="NSE", price=float(i)))
    store.close()
    # Verify all 10 events were written
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 10
