"""Unit tests for BrokerExecution — stale order eviction and timeout detection."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from ntrade.events.order import OrderFilledEvent, OrderIntentEvent
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus
from ntrade.execution.broker_executor import BrokerExecution


def _live_ctx(instrument):
    bus = EventBus()
    clock = LiveClock()
    return TradingContext(bus, clock, mode="live",
                          instruments={instrument.symbol: instrument}, session_id="")


def _placing_broker():
    """A mock broker whose place_order accepts and assigns an order id."""
    broker = MagicMock()

    def place(order):
        order.order_id = "D-1"
        return order

    broker.place_order.side_effect = place
    return broker


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


def test_poll_rate_limited_is_backoff_not_staleness():
    """H-5: a RateLimited during poll is quota backoff, NOT staleness — the
    order must stay tracked with its stale counter untouched (the old code
    counted DH-904s toward eviction and dropped live orders under rate
    pressure)."""
    from ntrade.execution.rate_limit import Quota, RateLimited

    bus = EventBus()
    clock = LiveClock()
    ctx = TradingContext(bus, clock, mode="live", instruments={}, session_id="")
    broker = MagicMock()
    broker.get_order_status.side_effect = RateLimited(Quota.ORDER)
    exe = BrokerExecution(ctx, broker)
    intent = OrderIntentEvent(
        symbol="X", exchange="NSE", side="BUY", quantity=10,
        order_type="LIMIT", price=100.0, ts=datetime.now(),
    )
    order = MagicMock()
    order.order_id = "RL-001"
    order.status = MagicMock()
    order.status.value = "PENDING"
    exe._open["RL-001"] = {
        "intent": intent, "order": order, "filled": 0,
        "status": order.status, "placed_at": datetime.now(),
    }
    for _ in range(15):  # well past _stale_limit — still must not evict
        exe.poll()
    assert "RL-001" in exe._open
    assert exe._open["RL-001"].get("stale", 0) == 0


def test_poll_transport_errors_evict_but_rate_limited_recovers():
    """H-5: real transport errors still count toward eviction, and a later
    successful refresh resets the counter."""
    from ntrade.execution.rate_limit import Quota, RateLimited

    bus = EventBus()
    clock = LiveClock()
    ctx = TradingContext(bus, clock, mode="live", instruments={}, session_id="")
    broker = MagicMock()
    broker.get_order_status.side_effect = RuntimeError("network down")
    exe = BrokerExecution(ctx, broker)
    intent = OrderIntentEvent(
        symbol="X", exchange="NSE", side="BUY", quantity=10,
        order_type="LIMIT", price=100.0, ts=datetime.now(),
    )
    order = MagicMock()
    order.order_id = "TE-001"
    order.status = MagicMock()
    order.status.value = "PENDING"
    exe._open["TE-001"] = {
        "intent": intent, "order": order, "filled": 0,
        "status": order.status, "placed_at": datetime.now(),
    }
    for _ in range(exe._stale_limit - 2):
        exe.poll()
    assert "TE-001" in exe._open
    assert exe._open["TE-001"]["stale"] == exe._stale_limit - 2
    # interleaved rate limits add nothing
    broker.get_order_status.side_effect = RateLimited(Quota.ORDER)
    exe.poll()
    assert exe._open["TE-001"]["stale"] == exe._stale_limit - 2
    # a successful refresh resets the counter
    broker.get_order_status.side_effect = lambda o: o
    exe.poll()
    assert exe._open["TE-001"]["stale"] == 0


# ------------------------------------------------------------------ C-3 / K-024
def test_submit_equity_defaults_to_cnc_delivery():
    """Kernel-routed equity orders must use the K-024 CNC default — the old
    hardcoded MIS turned delivery intents into intraday auto-square-offs."""
    from ntrade.domain.instruments.cash import Equity
    from ntrade.domain.orders.order import TradeType

    eq = Equity("RELIANCE")
    broker = _placing_broker()
    eq._broker = broker
    exe = BrokerExecution(_live_ctx(eq), broker)
    intent = OrderIntentEvent(symbol="RELIANCE", exchange="NSE", side="BUY",
                              quantity=10, order_type="LIMIT", price=2500.0,
                              ts=datetime.now())
    assert exe.submit(intent) is None
    placed = broker.place_order.call_args[0][0]
    assert placed.trade_type == TradeType.CNC


def test_submit_derivative_defaults_to_mis():
    from ntrade.domain.instruments.derivatives import Future
    from ntrade.domain.orders.order import TradeType

    fut = Future("NIFTY FUT")
    broker = _placing_broker()
    fut._broker = broker
    exe = BrokerExecution(_live_ctx(fut), broker)
    intent = OrderIntentEvent(symbol="NIFTY FUT", exchange="NFO", side="BUY",
                              quantity=25, order_type="LIMIT", price=24500.0,
                              ts=datetime.now())
    assert exe.submit(intent) is None
    placed = broker.place_order.call_args[0][0]
    assert placed.trade_type == TradeType.MIS


# ------------------------------------------------------------------ C-4
def _orphan_book(entries):
    from ntrade.domain.orders.book import OrderBook, OrderBookEntry
    return OrderBook(entries=tuple(
        OrderBookEntry(**e) for e in entries))


def test_reconcile_open_adopts_orphan_broker_order():
    from ntrade.domain.instruments.cash import Equity
    from ntrade.domain.orders.order import OrderStatus

    eq = Equity("RELIANCE")
    broker = MagicMock()
    eq._broker = broker
    exe = BrokerExecution(_live_ctx(eq), broker)
    broker.get_orderbook.return_value = _orphan_book([
        {"symbol": "RELIANCE", "order_id": "ORPH-1", "side": "BUY",
         "quantity": 10, "price": 2500.0, "status": "PENDING", "exchange": "NSE"},
        # terminal rows are never adopted
        {"symbol": "RELIANCE", "order_id": "DONE-1", "side": "BUY",
         "quantity": 5, "price": 2500.0, "status": "COMPLETED", "exchange": "NSE"},
    ])
    adopted = exe.reconcile_open()
    assert adopted == ["ORPH-1"]
    record = exe._open["ORPH-1"]
    assert record["intent"].symbol == "RELIANCE"
    assert record["order"].status == OrderStatus.PENDING
    # second pass: already tracked -> nothing adopted (idempotent)
    assert exe.reconcile_open() == []


def test_reconcile_open_adopted_fill_is_emitted():
    from ntrade.domain.instruments.cash import Equity
    from ntrade.domain.orders.order import OrderStatus

    eq = Equity("RELIANCE")
    broker = MagicMock()
    eq._broker = broker
    ctx = _live_ctx(eq)
    exe = BrokerExecution(ctx, broker)
    broker.get_orderbook.return_value = _orphan_book([
        {"symbol": "RELIANCE", "order_id": "ORPH-1", "side": "BUY",
         "quantity": 10, "price": 2500.0, "status": "PENDING", "exchange": "NSE"},
    ])
    exe.reconcile_open()

    def complete(order):
        order.status = OrderStatus.COMPLETED
        order.filled_qty = 10
        order.avg_price = 2500.0
        return order

    broker.get_order_status.side_effect = complete
    emitted = exe.poll()
    fills = [e for e in emitted if isinstance(e, OrderFilledEvent)]
    assert len(fills) == 1
    assert fills[0].order_id == "ORPH-1" and fills[0].quantity == 10
    assert "ORPH-1" not in exe._open  # terminal -> removed


def test_reconcile_open_unknown_symbol_is_not_adopted():
    from ntrade.domain.instruments.cash import Equity

    eq = Equity("RELIANCE")
    broker = MagicMock()
    eq._broker = broker
    exe = BrokerExecution(_live_ctx(eq), broker)
    broker.get_orderbook.return_value = _orphan_book([
        {"symbol": "UNKNOWN", "order_id": "ORPH-2", "side": "BUY",
         "quantity": 1, "price": 10.0, "status": "PENDING", "exchange": "NSE"},
    ])
    assert exe.reconcile_open() == []
    assert "ORPH-2" not in exe._open


def test_reconcile_open_rate_limited_propagates():
    from ntrade.domain.instruments.cash import Equity
    from ntrade.execution.rate_limit import Quota, RateLimited

    eq = Equity("RELIANCE")
    broker = MagicMock()
    eq._broker = broker
    exe = BrokerExecution(_live_ctx(eq), broker)
    broker.get_orderbook.side_effect = RateLimited(Quota.ORDER)
    with pytest.raises(RateLimited):
        exe.reconcile_open()
    assert exe._open == {}  # quota backoff never corrupts the tracker
