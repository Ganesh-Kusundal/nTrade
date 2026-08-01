"""TradingKernel event recording + record→replay equivalence (Slice E2)."""

from datetime import datetime, timedelta

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.storage.event_store import EventStore


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


def _ticks(n: int = 10):
    return [TickEvent(symbol="NIFTY", exchange="NSE", price=100.0 + i,
                      ts=datetime(2026, 1, 1, 9, 15) + timedelta(minutes=i))
            for i in range(n)]


def _fills(kernel):
    return [(e.order_id, e.fill_price, e.side)
            for e in kernel.bus.history if isinstance(e, OrderFilledEvent)]


def test_kernel_records_all_events():
    store = EventStore()
    k = TradingKernel(mode="live", clock=ReplayClock(), store=store)
    k.register(Equity("NIFTY"))
    k.register_strategy(BuyOnFirstTick())
    k.run_replay(_ticks())
    # every published event landed in the store
    assert len(store) == len(k.bus.history)
    assert len(store.events(TickEvent)) == len(_ticks())  # market events only
    assert len(store.events(OrderFilledEvent)) == 1


def test_store_market_events_excludes_derived():
    store = EventStore()
    k = TradingKernel(mode="live", clock=ReplayClock(), store=store)
    k.register(Equity("NIFTY"))
    k.register_strategy(BuyOnFirstTick())
    k.run_replay(_ticks())
    market = store.market_events()
    assert all(isinstance(e, TickEvent) for e in market)
    assert len(market) == len(_ticks())  # no fills/signals mixed in


def test_record_then_replay_identical_fills():
    """The zero-parity loop: record a live session, replay its market events
    through a fresh kernel, get identical fills."""
    store = EventStore()
    live = TradingKernel(mode="live", clock=ReplayClock(), store=store)
    live.register(Equity("NIFTY"))
    live.register_strategy(BuyOnFirstTick())
    live.run_replay(_ticks())
    live_fills = _fills(live)

    replay_store = EventStore()
    replayed = TradingKernel(mode="replay", clock=ReplayClock(), store=replay_store)
    replayed.register(Equity("NIFTY"))
    replayed.register_strategy(BuyOnFirstTick())
    replayed.run_replay(store.market_events())

    assert _fills(replayed) == live_fills
    assert live_fills == [("SIM-000001", 100.0, "BUY")]


def test_recording_jsonl_persists(tmp_path):
    path = tmp_path / "session.jsonl"
    store = EventStore(path=str(path))
    k = TradingKernel(mode="live", clock=ReplayClock(), store=store)
    k.register(Equity("NIFTY"))
    k.run_replay(_ticks(3))
    reloaded = EventStore(path=str(path))
    # store records derived events too (QuoteUpdated, CandleClosed, ...)
    assert len(reloaded.market_events()) == len(_ticks(3))
    assert reloaded.market_events()[0].price == 100.0
