"""Event traceability — correlation_id and causation_id propagation.

The EventBus stamps every published event with a correlation_id (groups all
events in one causal chain) and a causation_id (the immediate parent event_id).
Root events get correlation_id = their own event_id and causation_id = None.
Derived events (published from within a handler) inherit the chain's
correlation_id and get causation_id = the parent event's event_id.
"""

from datetime import datetime

from ntrade.events.base import Event
from ntrade.events.market import CandleClosedEvent, QuoteUpdatedEvent, TickEvent
from ntrade.events.risk import SignalApprovedEvent, SignalGeneratedEvent
from ntrade.kernel.event_bus import EventBus


def _ts(minute: int = 0):
    from datetime import timedelta
    return datetime(2026, 8, 2, 9, 15) + timedelta(minutes=minute)


# --------------------------------------------------------------- root events

def test_root_event_gets_own_correlation_id():
    """A published event with no parent gets correlation_id = its own event_id."""
    bus = EventBus()
    tick = TickEvent(ts=_ts(), symbol="X", exchange="NSE", price=1.0)
    bus.publish(tick)
    assert tick.correlation_id == tick.event_id
    assert tick.causation_id is None


def test_two_root_events_have_separate_correlations():
    """Independent root events get different correlation_ids."""
    bus = EventBus()
    t1 = TickEvent(ts=_ts(), symbol="X", exchange="NSE", price=1.0)
    t2 = TickEvent(ts=_ts(1), symbol="X", exchange="NSE", price=2.0)
    bus.publish(t1)
    bus.publish(t2)
    assert t1.correlation_id != t2.correlation_id
    assert t1.correlation_id == t1.event_id
    assert t2.correlation_id == t2.event_id


# -------------------------------------------------------- nested propagation

def test_nested_event_inherits_correlation_and_causation():
    """An event published from within a handler inherits correlation and causation."""
    bus = EventBus()
    derived = []

    def on_tick(event):
        child = QuoteUpdatedEvent(
            symbol=event.symbol, exchange=event.exchange,
            ltp=event.price, ts=event.ts,
        )
        bus.publish(child)
        derived.append(child)

    bus.subscribe(TickEvent, on_tick)
    tick = TickEvent(ts=_ts(), symbol="X", exchange="NSE", price=100.0)
    bus.publish(tick)

    assert tick.correlation_id == tick.event_id
    child = derived[0]
    assert child.correlation_id == tick.correlation_id
    assert child.causation_id == tick.event_id


def test_full_causal_chain_shares_correlation_id():
    """Tick → QuoteUpdated → Signal → SignalApproved all share one correlation_id.

    Each event's causation_id points to its *immediate* parent, not the root.
    """
    bus = EventBus()
    chain = []

    def on_tick(event):
        bus.publish(QuoteUpdatedEvent(
            symbol=event.symbol, exchange=event.exchange,
            ltp=event.price, ts=event.ts,
        ))

    def on_quote(event):
        chain.append(event)
        bus.publish(SignalGeneratedEvent(
            symbol=event.symbol, exchange=event.exchange,
            side="BUY", quantity=10, price=event.ltp,
            strategy="test", ts=event.ts,
        ))

    def on_signal(event):
        chain.append(event)
        bus.publish(SignalApprovedEvent(signal=event, ts=event.ts))

    def on_approved(event):
        chain.append(event)

    bus.subscribe(TickEvent, on_tick)
    bus.subscribe(QuoteUpdatedEvent, on_quote)
    bus.subscribe(SignalGeneratedEvent, on_signal)
    bus.subscribe(SignalApprovedEvent, on_approved)

    tick = TickEvent(ts=_ts(), symbol="X", exchange="NSE", price=100.0)
    bus.publish(tick)

    quote, signal, approved = chain

    # All events share the tick's correlation_id
    assert tick.correlation_id == quote.correlation_id == signal.correlation_id == approved.correlation_id

    # Causation chain: tick → quote → signal → approved
    assert quote.causation_id == tick.event_id
    assert signal.causation_id == quote.event_id
    assert approved.causation_id == signal.event_id


# -------------------------------------------------------- explicit overrides

def test_explicit_correlation_id_is_preserved():
    """A caller-set correlation_id is never overwritten by the bus."""
    bus = EventBus()
    cid = "manual-correlation-abc"
    tick = TickEvent(ts=_ts(), symbol="X", exchange="NSE",
                     price=1.0, correlation_id=cid)
    bus.publish(tick)
    assert tick.correlation_id == cid


def test_explicit_causation_id_is_preserved():
    """A caller-set causation_id is never overwritten by the bus."""
    bus = EventBus()
    cause = "some-cause-id"
    tick = TickEvent(ts=_ts(), symbol="X", exchange="NSE",
                     price=1.0, causation_id=cause)
    bus.publish(tick)
    assert tick.causation_id == cause


# --------------------------------------------------- EventStore round-trip

def test_event_store_encodes_traceability():
    """EventStore serializes/deserializes correlation and causation IDs."""
    from ntrade.storage.event_store import EventStore

    tick = TickEvent(ts=_ts(), symbol="X", exchange="NSE", price=1.0)
    bus = EventBus()
    bus.publish(tick)

    store = EventStore()
    store.append(tick)

    events = store.events()
    assert len(events) == 1
    assert events[0].correlation_id == tick.correlation_id
    assert events[0].causation_id is None


def test_event_store_decodes_old_json_without_traceability():
    """Old JSONL records without correlation_id/causation_id decode correctly."""
    import json
    import tempfile
    from pathlib import Path
    from ntrade.storage.event_store import EventStore

    old_record = json.dumps({
        "__type__": "TickEvent",
        "ts": "2026-08-02T09:15:00",
        "event_id": "abc123",
        "symbol": "X",
        "exchange": "NSE",
        "price": 100.0,
        "quantity": 0,
        "side": "",
        "kind": "trade",
    })

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "events.jsonl"
        path.write_text(old_record + "\n", encoding="utf-8")
        store = EventStore(path=str(path))
        events = store.events()
        assert len(events) == 1
        assert events[0].event_id == "abc123"
        assert events[0].correlation_id is None
        assert events[0].causation_id is None


# -------------------------------------------------------- bus clear hygiene

def test_bus_clear_resets_stack():
    """After bus.clear(), the dispatch stack is empty — no stale parent leaks."""
    bus = EventBus()
    tick = TickEvent(ts=_ts(), symbol="X", exchange="NSE", price=1.0)
    bus.publish(tick)
    bus.clear()

    # A new root event after clear should get its own correlation_id
    tick2 = TickEvent(ts=_ts(1), symbol="X", exchange="NSE", price=2.0)
    bus.publish(tick2)
    assert tick2.correlation_id == tick2.event_id
    assert tick2.causation_id is None
