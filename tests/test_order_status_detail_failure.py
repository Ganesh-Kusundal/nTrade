"""A failed order-detail fetch must surface, not silently zero the fill.

Regression: get_order_status swallowed get_order_detail errors, so a
COMPLETED order with a failed detail read kept filled_qty=0, BrokerExecution
emitted no OrderFilledEvent, and the fill was invisible to strategy/risk/
audit until the 60s position sync papered over it.
"""
from types import SimpleNamespace

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.domain.orders.order import Order, OrderSide, OrderStatus, OrderType


def _order():
    return Order(instrument=Equity("NIFTY"), side=OrderSide.BUY, quantity=50,
                 order_type=OrderType.MARKET, order_id="BRK-1",
                 status=OrderStatus.PENDING)


def test_detail_fetch_failure_raises():
    from ntrade.brokers.dhan import DhanBroker

    broker = DhanBroker.__new__(DhanBroker)
    broker._transport = SimpleNamespace(
        get_order_status=lambda oid: "TRADED",
        get_order_detail=lambda oid: (_ for _ in ()).throw(RuntimeError("detail boom")),
    )
    with pytest.raises(RuntimeError, match="detail"):
        broker.get_order_status(_order())
