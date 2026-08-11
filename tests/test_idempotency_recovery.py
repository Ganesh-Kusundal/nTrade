"""Test: rebuild idempotency guard from EventStore on crash recovery.

After a crash, the idempotency guard (which prevents duplicate order submissions)
is lost. We need to rebuild it from the EventStore by replaying correlation_ids
from OrderAcceptedEvents so that duplicate retries are detected.
"""
from datetime import datetime
from ntrade.storage.event_store import EventStore
from ntrade.events.order import OrderAcceptedEvent, OrderFilledEvent
from ntrade.execution._guard import MemoryIdempotencyGuard, CorrelationId


def test_recovered_fills_seed_idempotency_guard():
    """After recovery, the guard should detect duplicate correlation_ids."""
    store = EventStore()
    # Simulate a crashed session that recorded these events
    store.append(OrderAcceptedEvent(
        ts=datetime.now(), order_id="BRK-001", symbol="REL",
        exchange="NSE", side="BUY", quantity=10, strategy="s1",
        correlation_id="corr-abc",
    ))
    store.append(OrderFilledEvent(
        ts=datetime.now(), order_id="BRK-001", symbol="REL",
        exchange="NSE", side="BUY", quantity=10, fill_price=100.0,
        strategy="s1", correlation_id="corr-abc",
    ))

    guard = MemoryIdempotencyGuard()
    
    # Replay correlation IDs from OrderAcceptedEvents in the store
    # Note: recovery_events() only returns market data + fills, so we iterate
    # the store directly to find OrderAcceptedEvents
    for event in store.events(OrderAcceptedEvent):
        if event.correlation_id:
            cid = CorrelationId(value=event.correlation_id)
            guard.record_result(cid, event.order_id)

    # Now a retry with the same correlation_id should be detected as duplicate
    result = guard.check_and_reserve(CorrelationId(value="corr-abc"))
    assert result is not None, "Expected duplicate detection"
    assert result.result == "BRK-001", f"Expected order_id BRK-001, got {result.result}"


def test_multiple_correlation_ids_recovered():
    """Multiple accepted orders should all be recovered into the guard."""
    store = EventStore()
    store.append(OrderAcceptedEvent(
        ts=datetime.now(), order_id="BRK-001", symbol="REL",
        exchange="NSE", side="BUY", quantity=10, strategy="s1",
        correlation_id="corr-1",
    ))
    store.append(OrderAcceptedEvent(
        ts=datetime.now(), order_id="BRK-002", symbol="TCS",
        exchange="NSE", side="SELL", quantity=5, strategy="s1",
        correlation_id="corr-2",
    ))

    guard = MemoryIdempotencyGuard()
    for event in store.events(OrderAcceptedEvent):
        if event.correlation_id:
            cid = CorrelationId(value=event.correlation_id)
            guard.record_result(cid, event.order_id)

    # Both should be detected as duplicates
    result1 = guard.check_and_reserve(CorrelationId(value="corr-1"))
    assert result1 is not None
    assert result1.result == "BRK-001"

    result2 = guard.check_and_reserve(CorrelationId(value="corr-2"))
    assert result2 is not None
    assert result2.result == "BRK-002"

    # A new correlation_id should NOT be detected as duplicate
    result3 = guard.check_and_reserve(CorrelationId(value="corr-new"))
    assert result3 is None, "New correlation_id should not be detected as duplicate"


def test_order_accepted_without_correlation_id_skipped():
    """OrderAcceptedEvents without correlation_id should be skipped."""
    store = EventStore()
    store.append(OrderAcceptedEvent(
        ts=datetime.now(), order_id="BRK-001", symbol="REL",
        exchange="NSE", side="BUY", quantity=10, strategy="s1",
        # No correlation_id - should be skipped
    ))

    guard = MemoryIdempotencyGuard()
    for event in store.events(OrderAcceptedEvent):
        if event.correlation_id:
            cid = CorrelationId(value=event.correlation_id)
            guard.record_result(cid, event.order_id)

    # Guard should be empty - no correlation_ids to recover
    result = guard.check_and_reserve(CorrelationId(value="corr-abc"))
    assert result is None, "Guard should be empty when no correlation_ids recovered"
