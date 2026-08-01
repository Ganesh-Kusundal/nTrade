"""ResilientKernel crash recovery tests (Slice F1 + H3 partial-fill deltas)."""

import types
from datetime import datetime, timedelta

import pytest

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.events.order import (
    OrderAcceptedEvent, OrderFilledEvent, OrderRejectedEvent, OrderUpdatedEvent,
)
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.kernel.resilient import ResilientKernel
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


def _ts(minute: int = 0):
    return datetime(2026, 1, 1, 9, 15) + timedelta(minutes=minute)


def _ticks(n: int = 10):
    return [TickEvent(symbol="NIFTY", exchange="NSE", price=100.0 + i,
                      ts=_ts(i)) for i in range(n)]


def _record_live_session(n: int = 10) -> EventStore:
    """A store holding a live session with one fill (buy 5 @ 100)."""
    store = EventStore()
    kernel = TradingKernel(mode="live", clock=ReplayClock(), store=store)
    kernel.register(Equity("NIFTY"))
    kernel.register_strategy(BuyOnFirstTick())
    kernel.run_replay(_ticks(n))
    return store


def test_store_recovery_events_include_fills():
    store = _record_live_session()
    recovery = store.recovery_events()
    # market ticks + the one fill — no derived signals/candles/balance
    assert any(isinstance(e, OrderFilledEvent) for e in recovery)
    assert not any(type(e).__name__ == "SignalGeneratedEvent" for e in recovery)
    assert not any(type(e).__name__ == "BalanceChangedEvent" for e in recovery)


def test_store_recovery_events_causal_order():
    """Fills must replay AFTER the market event that caused them (recorded
    order is effect-then-cause, so recovery_events re-sorts)."""
    from ntrade.events.market import TickEvent

    store = _record_live_session()
    recovery = store.recovery_events()
    tick_ts = [e.ts for e in recovery if isinstance(e, TickEvent)]
    fill_ts = [e.ts for e in recovery if isinstance(e, OrderFilledEvent)]
    assert tick_ts == sorted(tick_ts)
    # at the fill's timestamp, the causal tick comes first
    assert any(t <= f for t, f in zip(tick_ts, fill_ts + [fill_ts[-1]]))


def test_recover_is_one_shot():
    store = _record_live_session()
    rk = ResilientKernel(store=store, mode="replay", clock=ReplayClock(),
                         initial_cash=100_000.0)
    rk.register(Equity("NIFTY"))
    rk.recover()
    assert rk.recovered_events > 0
    with pytest.raises(RuntimeError, match="already ran"):
        rk.recover()


def test_recover_reseeds_execution_sequence():
    """After recovery, the first new order must not collide with recovered ids."""
    from ntrade.events.order import OrderIntentEvent

    store = _record_live_session()
    rk = ResilientKernel(store=store, mode="replay", clock=ReplayClock(),
                         initial_cash=100_000.0)
    rk.register(Equity("NIFTY"))
    rk.recover()
    rk.register_strategy(BuyOnFirstTick())
    rk.run_replay([TickEvent(symbol="NIFTY", exchange="NSE", price=101.0, ts=_ts(20))])
    new_fills = [e for e in rk.bus.history if isinstance(e, OrderFilledEvent)]
    assert len(new_fills) == 2  # recovered fill + the new one
    recovered_id = [e.order_id for e in store.recovery_events()
                    if isinstance(e, OrderFilledEvent)][0]
    assert new_fills[1].order_id != recovered_id  # new id continues past recovery
    assert len({f.order_id for f in new_fills}) == 2  # no collision


def test_recover_rebuilds_position_and_balance():
    store = _record_live_session()
    rk = ResilientKernel(store=store, mode="replay", clock=ReplayClock(),
                         initial_cash=100_000.0)
    rk.register(Equity("NIFTY"))
    rk.recover()
    pos = rk.ctx.portfolio.position("NIFTY")
    assert pos is not None
    assert pos.quantity == 5
    assert pos.avg_price == 100.0
    assert rk.ctx.account.balance == pytest.approx(100_000.0 - 5 * 100.0)
    assert rk.recovered_events == len(store.recovery_events())
    assert rk.snapshot()["balance"] == pytest.approx(100_000.0 - 500.0)


def test_recover_matches_original_kernel_state():
    """The zero-parity recovery loop: rebuilt state == crashed session state."""
    store = _record_live_session()
    original = TradingKernel(mode="live", clock=ReplayClock(), initial_cash=100_000.0)
    original.register(Equity("NIFTY"))
    original.register_strategy(BuyOnFirstTick())
    original.run_replay(_ticks(10))
    original_fills = [e for e in original.bus.history if isinstance(e, OrderFilledEvent)]
    original_pos = original.ctx.portfolio.position("NIFTY")

    rk = ResilientKernel(store=store, mode="replay", clock=ReplayClock(),
                         initial_cash=100_000.0)
    rk.register(Equity("NIFTY"))
    rk.recover()
    recovered_fills = [e for e in rk.bus.history if isinstance(e, OrderFilledEvent)]

    def _fill_key(f):
        # event_id is regenerated per run; compare the causal content instead
        return (f.order_id, f.symbol, f.side, f.quantity, f.fill_price, f.strategy)

    assert [(_fill_key(f)) for f in recovered_fills] == [_fill_key(f) for f in original_fills]
    assert len(recovered_fills) == len(original_fills)
    assert rk.ctx.portfolio.position("NIFTY").quantity == original_pos.quantity
    assert rk.ctx.portfolio.position("NIFTY").avg_price == original_pos.avg_price
    assert rk.ctx.account.balance == original.ctx.account.balance
    # candle state rebuilt too (content equal; event_id is regenerated)
    def _candle_key(c):
        return (c.ts, c.symbol, c.timeframe, c.open, c.high, c.low, c.close, c.volume)

    assert [_candle_key(c) for c in rk.candle_engine.candles("NIFTY")] == \
        [_candle_key(c) for c in original.candle_engine.candles("NIFTY")]


def test_recover_does_not_retrade():
    """Recovery replays the causal stream — no strategies attached, so the
    recovered kernel produces no new signals/intents beyond the recorded fills."""
    store = _record_live_session()
    rk = ResilientKernel(store=store, mode="replay", clock=ReplayClock())
    rk.register(Equity("NIFTY"))
    rk.recover()
    # no new trade signals, order intents or order acceptances — the only
    # order-side events are the replayed fills themselves
    from ntrade.events.order import OrderAcceptedEvent, OrderIntentEvent
    from ntrade.events.risk import SignalGeneratedEvent

    assert not [e for e in rk.bus.history if isinstance(e, SignalGeneratedEvent)]
    assert not [e for e in rk.bus.history if isinstance(e, OrderIntentEvent)]
    assert not [e for e in rk.bus.history if isinstance(e, OrderAcceptedEvent)]
    fills = [e for e in rk.bus.history if isinstance(e, OrderFilledEvent)]
    assert len(fills) == 1  # exactly the recorded fill, applied once


def test_recover_requires_empty_strategies():
    store = _record_live_session()
    rk = ResilientKernel(store=store, mode="replay", clock=ReplayClock())
    rk.register(Equity("NIFTY"))
    rk.register_strategy(BuyOnFirstTick())
    with pytest.raises(RuntimeError, match="recover"):
        rk.recover()


def test_recover_without_store_raises():
    rk = ResilientKernel(store=None)
    with pytest.raises(RuntimeError, match="recovery store"):
        rk.recover()


def test_recover_empty_store_is_noop():
    rk = ResilientKernel(store=EventStore())
    rk.recover()
    assert rk.recovered_events == 0


def test_recover_from_jsonl_persistence(tmp_path):
    """A crash is survived by a fresh process reading the JSONL store."""
    path = tmp_path / "session.jsonl"
    store = EventStore(path=str(path))
    kernel = TradingKernel(mode="live", clock=ReplayClock(), store=store)
    kernel.register(Equity("NIFTY"))
    kernel.register_strategy(BuyOnFirstTick())
    kernel.run_replay(_ticks(5))

    # "restart": read the same file in a brand-new ResilientKernel
    reloaded = EventStore(path=str(path))
    rk = ResilientKernel(store=reloaded, mode="replay", clock=ReplayClock(),
                         initial_cash=100_000.0)
    rk.register(Equity("NIFTY"))
    rk.recover()
    pos = rk.ctx.portfolio.position("NIFTY")
    assert pos is not None and pos.quantity == 5
    assert rk.last_event_ts() == _ts(4)


def test_recover_records_recovered_session_to_separate_store():
    """With ``record=`` the recovered session continues auditing (and the
    recovery replay itself is not re-appended)."""
    store = _record_live_session()
    record = EventStore()
    rk = ResilientKernel(store=store, mode="replay", clock=ReplayClock(),
                         record=record)
    rk.register(Equity("NIFTY"))
    rk.recover()
    # recovery did not re-append the causal stream to the record store
    assert len(record.events(TickEvent)) == 0
    # but a subsequent live event is recorded
    rk.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=105.0, ts=_ts(20)))
    assert len(record.events(TickEvent)) == 1


# ---------------------------------------------------------------------------
# H3 — partial-fill delta state rebuildable on recovery


def _make_broker(**tsl_methods) -> DhanBroker:
    """A real DhanBroker wired to a stubbed Tradehull (like test_live_execution)."""
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(**tsl_methods)
    return broker


def _partial_fill_store(order_id: str = "ORD-300", quantity: int = 10,
                        filled: int = 3) -> EventStore:
    """A store recording one accepted order with a partial fill, still open."""
    store = EventStore()
    store.append(OrderAcceptedEvent(
        order_id=order_id, symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=quantity, strategy="buy_first", ts=_ts(0)))
    store.append(OrderFilledEvent(
        order_id=order_id, symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=filled, fill_price=100.0, strategy="buy_first", ts=_ts(1)))
    store.append(OrderUpdatedEvent(
        order_id=order_id, symbol="NIFTY", exchange="NSE", side="BUY",
        status="PARTIALLY_FILLED", filled_qty=filled, avg_price=100.0,
        strategy="buy_first", ts=_ts(1)))
    return store


def _partial_broker():
    """A stub broker that reports PARTIAL until flipped to COMPLETE."""
    state = {"status": "PARTIAL"}

    def order_placement(**kw):
        return "ORD-300"

    def get_order_status(orderid=None, **kw):
        return state["status"]

    def get_order_detail(orderid=None, **kw):
        return {"orderId": orderid, "orderStatus": state["status"],
                "filledQty": 10 if state["status"] == "COMPLETE" else 3,
                "avgPrice": 100.0}

    broker = _make_broker(order_placement=order_placement,
                          get_order_status=get_order_status,
                          get_order_detail=get_order_detail)
    return broker, state


def test_store_open_order_deltas_reconstructs_partial_fill():
    """The store can rebuild the per-order filled/remaining delta (H3)."""
    deltas = _partial_fill_store().open_order_deltas()
    assert set(deltas) == {"ORD-300"}
    d = deltas["ORD-300"]
    assert d["quantity"] == 10
    assert d["filled"] == 3
    assert d["remaining"] == 7
    assert d["side"] == "BUY"
    assert d["strategy"] == "buy_first"


def test_store_open_order_deltas_excludes_terminal_orders():
    """Fully-filled, completed, rejected and cancelled orders are not open."""
    store = _partial_fill_store()
    # fully filled + completed
    store.append(OrderAcceptedEvent(
        order_id="ORD-301", symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=5, strategy="", ts=_ts(2)))
    store.append(OrderFilledEvent(
        order_id="ORD-301", symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=5, fill_price=100.0, ts=_ts(3)))
    store.append(OrderUpdatedEvent(
        order_id="ORD-301", symbol="NIFTY", exchange="NSE", side="BUY",
        status="COMPLETED", filled_qty=5, avg_price=100.0, ts=_ts(3)))
    # rejected at placement
    store.append(OrderAcceptedEvent(
        order_id="ORD-302", symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=5, strategy="", ts=_ts(4)))
    store.append(OrderRejectedEvent(
        order_id="ORD-302", symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=5, reason="insufficient margin", ts=_ts(5)))
    # partial then cancelled
    store.append(OrderAcceptedEvent(
        order_id="ORD-303", symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=5, strategy="", ts=_ts(6)))
    store.append(OrderFilledEvent(
        order_id="ORD-303", symbol="NIFTY", exchange="NSE", side="BUY",
        quantity=2, fill_price=100.0, ts=_ts(7)))
    store.append(OrderUpdatedEvent(
        order_id="ORD-303", symbol="NIFTY", exchange="NSE", side="BUY",
        status="CANCELLED", filled_qty=2, avg_price=100.0, ts=_ts(7)))
    deltas = store.open_order_deltas()
    assert set(deltas) == {"ORD-300"}  # only the still-open partial fill


def test_recover_rebuilds_open_order_deltas():
    """Recovery rehydrates the executor's open-order tracker with the
    partial-fill delta so the remaining quantity survives the crash."""
    store = _partial_fill_store()
    broker, _ = _partial_broker()
    rk = ResilientKernel(store=store, mode="live", clock=ReplayClock(),
                         broker=broker, initial_cash=100_000.0)
    rk.register(Equity("NIFTY", broker=broker))
    rk.recover()
    exe = rk.broker_execution()
    assert exe is not None
    assert exe.open_orders() == ["ORD-300"]
    record = exe._open["ORD-300"]
    assert record["filled"] == 3          # already-filled delta preserved
    assert record["intent"].quantity == 10  # original order size known
    assert record["order"].order_id == "ORD-300"
    assert record["order"].filled_qty == 3
    # recovered fill applied exactly once to the portfolio
    pos = rk.ctx.portfolio.position("NIFTY")
    assert pos is not None and pos.quantity == 3


def test_recover_resumed_poll_emits_only_remaining_delta():
    """After recovery, poll() emits only the remaining 7 — never re-emits
    the already-filled 3 (no double-count on the resumed lifecycle)."""
    store = _partial_fill_store()
    broker, state = _partial_broker()
    rk = ResilientKernel(store=store, mode="live", clock=ReplayClock(),
                         broker=broker, initial_cash=100_000.0)
    rk.register(Equity("NIFTY", broker=broker))
    rk.recover()
    assert rk.broker_execution().open_orders() == ["ORD-300"]

    state["status"] = "COMPLETE"
    emitted = rk.poll_orders()
    fills = [e for e in emitted if isinstance(e, OrderFilledEvent)]
    assert len(fills) == 1 and fills[0].quantity == 7  # remaining only
    assert rk.broker_execution().open_orders() == []    # now terminal
    pos = rk.ctx.portfolio.position("NIFTY")
    assert pos is not None and pos.quantity == 10  # 3 recovered + 7 resumed


def test_recover_reseed_prevents_brk_id_collision():
    """New orders after recovery must not collide with restored BRK- ids."""
    from ntrade.events.order import OrderIntentEvent

    store = _partial_fill_store(order_id="BRK-000042", quantity=5, filled=2)
    # a broker that never assigns its own id → the BRK- fallback must fire
    broker = _make_broker(
        order_placement=lambda **kw: None,
        get_order_status=lambda orderid=None, **kw: "COMPLETE",
        get_order_detail=lambda orderid=None, **kw: {
            "orderId": orderid, "orderStatus": "COMPLETE",
            "filledQty": 5, "avgPrice": 100.0},
    )
    rk = ResilientKernel(store=store, mode="live", clock=ReplayClock(),
                         broker=broker, initial_cash=100_000.0)
    rk.register(Equity("NIFTY", broker=broker))
    rk.recover()
    exe = rk.broker_execution()
    assert exe is not None
    assert exe._seq >= 42  # restored order bumped the sequence
    intent = OrderIntentEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                              quantity=1, price=100.0, strategy="", ts=_ts(10))
    outcome = exe.submit(intent)
    assert outcome is None  # accepted (not rejected)
    # the fresh order got the next BRK- id — strictly past the restored one
    new_ids = [oid for oid in exe.open_orders() if oid != "BRK-000042"]
    assert new_ids == ["BRK-000043"]
