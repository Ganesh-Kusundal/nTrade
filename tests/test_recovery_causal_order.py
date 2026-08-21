"""recovery_events must replay market-first: a fill must never run against
pre-tick instrument state.

The bus records effect-then-cause (the base-Event store handler runs LAST in
MRO dispatch, so the fill a tick triggered is appended BEFORE that tick).
Recovery therefore needs cause-then-effect: on a timestamp tie (live fill ts
= ctx.now() at poll time; tick ts = clock at ingest — they tie constantly)
the MARKET event must sort first. A plain ts sort preserves append order on
ties, i.e. fill-first — exactly the inversion this prevents.
"""
from datetime import datetime

from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.storage.event_store import EventStore


def test_market_event_replays_before_its_fill_on_ts_tie():
    store = EventStore()
    ts = datetime(2026, 8, 21, 9, 15)
    # Recorded order (effect-then-cause): fill lands BEFORE its tick.
    store.append(OrderFilledEvent(order_id="SIM-1", symbol="NIFTY", exchange="NSE",
                                  side="BUY", quantity=5, fill_price=100.0, ts=ts))
    store.append(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=ts))

    recovered = store.recovery_events()
    assert isinstance(recovered[0], TickEvent), (
        "tick must replay before the fill it caused")
    assert isinstance(recovered[1], OrderFilledEvent)


def test_recovery_filters_to_market_and_fills():
    store = EventStore()
    ts = datetime(2026, 8, 21, 9, 15)
    store.append(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=ts))
    from ntrade.events.risk import SignalGeneratedEvent
    store.append(SignalGeneratedEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                                      quantity=1, ts=ts))
    recovered = store.recovery_events()
    assert all(not isinstance(e, SignalGeneratedEvent) for e in recovered)
