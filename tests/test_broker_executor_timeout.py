"""Tests for configurable order timeout in BrokerExecution — Task 4.

Scalpers need sub-30s timeouts; the 5-minute default is too long for aggressive
scalping where stale orders accumulate capital. The timeout must be configurable
without changing the BrokerExecution call site in TradingKernel.
"""

import pytest

from ntrade.events.order import OrderRejectedEvent, OrderIntentEvent
from ntrade.execution.broker_executor import BrokerExecution
from ntrade.domain.orders.order import OrderStatus
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus


class _StubBroker:
    """Minimal broker that accepts orders and never fills them."""

    def get_order_status(self, order):
        order.status = OrderStatus.PENDING
        return order

    def get_instrument_metadata(self, instrument):
        return {}


def _make_ctx():
    bus = EventBus()
    clock = LiveClock()
    return TradingContext(bus, clock, mode="live",
                          instruments={}, session_id="test")


def test_order_timeout_defaults_to_300():
    """Default timeout is 300s (5 minutes) — backward compatible."""
    ctx = _make_ctx()
    exe = BrokerExecution(ctx, _StubBroker())
    assert exe._order_timeout_seconds == 300.0


def test_order_timeout_is_configurable():
    """A custom timeout is honored and stored as float."""
    ctx = _make_ctx()
    exe = BrokerExecution(ctx, _StubBroker(), order_timeout_seconds=30)
    assert exe._order_timeout_seconds == 30.0


def test_order_timeout_threaded_through_kernel():
    """TradingKernel forwards order_timeout_seconds to BrokerExecution."""
    from ntrade.kernel.session import TradingKernel

    class _StubBrokerWithStop(_StubBroker):
        def get_balance(self):
            return 100_000.0

    broker = _StubBrokerWithStop()
    k = TradingKernel(mode="live", broker=broker,
                      order_timeout_seconds=45)
    exe = k.broker_execution()
    assert exe is not None
    assert exe._order_timeout_seconds == 45.0


def test_order_timeout_zero_means_no_timeout():
    """order_timeout_seconds=0 disables timeout (never times out)."""
    ctx = _make_ctx()
    exe = BrokerExecution(ctx, _StubBroker(), order_timeout_seconds=0)
    assert exe._order_timeout_seconds == 0.0
