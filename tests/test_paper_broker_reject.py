"""PaperBroker must reject MARKET orders with no real market price.

Critical #2: PaperBroker.get_quote auto-seeds unknown symbols at 100.0, and
place_order previously fell back to that minted quote — so a paper MARKET
order on a symbol with no market data filled at a fabricated 100.00,
polluting paper PnL and pre-deploy gate evidence.
"""

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.orders.order import OrderType


def test_paper_market_order_no_price_rejected():
    """A paper MARKET order with no live quote, no reference_price, and no
    seeded quote must be rejected — never filled at a minted 100.0."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    with pytest.raises(ValueError, match="no market price"):
        rel.order.place("BUY", 10, order_type=OrderType.MARKET)
    assert broker.orders == []  # nothing recorded, no phantom fill


def test_paper_market_order_fills_at_reference_price_without_live_quote():
    """Zero-parity path: a reference_price (bar close) is a real price even
    with no live quote — the order must fill at it."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.place(
        "BUY", 10, order_type=OrderType.MARKET, reference_price=116.04)
    assert order.status.value == "COMPLETED"
    assert order.avg_price == 116.04


def test_paper_market_order_fills_at_live_ltp():
    """A live instrument quote is a real market price — fill at it."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    rel._quote = rel._quote.with_update(ltp=2500.0)
    order = rel.order.place("BUY", 10, order_type=OrderType.MARKET)
    assert order.status.value == "COMPLETED"
    assert order.avg_price == 2500.0


def test_paper_market_order_fills_at_seeded_quote():
    """A deliberately seeded quote is a real price — fill at it (this is the
    static-test path: seed_quote then market order)."""
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.place("BUY", 10, order_type=OrderType.MARKET)
    assert order.status.value == "COMPLETED"
    assert order.avg_price == 2500.0


def test_paper_limit_order_fills_at_price():
    """A LIMIT order with an explicit price fills at that price (unchanged)."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(10, price=99.5)
    assert order.status.value == "COMPLETED"
    assert order.avg_price == 99.5


def test_paper_get_quote_still_seeds_for_data_reads():
    """Guard: get_quote must keep seeding for data reads (history/chains/
    depth and static tests depend on it)."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    assert broker.get_quote(rel).ltp == 100.0
