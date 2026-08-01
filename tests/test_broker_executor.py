"""Unit tests for BrokerExecution — stale order eviction and timeout detection."""

from datetime import datetime
from unittest.mock import MagicMock

from ntrade.events.order import OrderIntentEvent
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus
from ntrade.execution.broker_executor import BrokerExecution


def test_stale_order_evicted_after_max_failures():
    bus = EventBus()
    clock = LiveClock()
    ctx = TradingContext(bus, clock, mode="live", instruments={}, session_id="")

    broker = MagicMock()
    broker.get_order_status.side_effect = RuntimeError("network error")

    exe = BrokerExecution(ctx, broker)
    intent = OrderIntentEvent(
        symbol="X", exchange="NSE", side="BUY", quantity=10,
        order_type="LIMIT", price=100.0, ts=datetime.now(),
    )
    # Manually inject an open order
    order = MagicMock()
    order.order_id = "TEST-001"
    order.status = MagicMock()
    order.status.value = "PENDING"
    exe._open["TEST-001"] = {
        "intent": intent, "order": order, "filled": 0,
        "status": order.status, "placed_at": datetime.now(),
    }
    # Poll enough times to trigger eviction
    for _ in range(15):
        exe.poll()
    assert "TEST-001" not in exe._open
