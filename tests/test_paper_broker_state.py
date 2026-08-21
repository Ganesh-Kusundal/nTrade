"""PaperBroker must report authoritative balance + positions from its fills
(so PositionSyncEngine reconciles paper to its own reality instead of wiping
the kernel). Regression for the paper/synth position-sync wipe bug."""

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.orders.order import OrderType


def test_paper_get_quote_never_mints_placeholder():
    """get_quote must never fabricate a 100.0 quote for an unseeded symbol —
    paper has no real market price until the feed seeds one."""
    broker = PaperBroker()
    with pytest.raises(ValueError, match="no live quote"):
        broker.get_quote("NIFTY OCT FUT")


def test_paper_broker_reports_authoritative_positions_static_balance():
    """Positions are the broker book's authority; cash is NOT — the kernel's
    PortfolioEngine is the single cash ledger (Task 2.1). PaperBroker's
    balance stays at the seeded opening cash so get_balance() reports session
    start; PositionSyncEngine skips cash for brokers with reports_cash=False."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    broker.seed_quote("RELIANCE", 100.0)

    # Buy 10 @ 100 -> a position appears; broker cash untouched.
    rel.order.place("BUY", 10, order_type=OrderType.MARKET)
    assert broker.get_balance() == 100_000.0, (
        "broker must not mutate cash — the kernel ledger owns it")
    pos = broker.get_positions()
    assert len(pos) == 1 and pos[0].quantity == 10, "position must be tracked"

    # Sell 10 @ 101 -> back to flat.
    rel.order.place("SELL", 10, order_type=OrderType.MARKET, price=101.0)
    assert broker.get_positions() == [], "position must close on the sell"
    assert broker.get_balance() == 100_000.0, "cash still at seeded opening"


def test_paper_broker_short_round_trip_does_not_crash():
    """Regression: BUY-flattening a short hit ZeroDivisionError (the BUY
    branch lacked the qty==0 guard). Also checks partial-cover avg_price."""
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    broker.seed_quote("RELIANCE", 100.0)

    # Open a short: SELL 10 @ 100 -> qty -10.
    rel.order.place("SELL", 10, order_type=OrderType.MARKET, price=100.0)
    assert broker.get_positions()[0].quantity == -10

    # Partial cover: BUY 4 @ 105 -> qty -6; avg_price keeps the entry (100).
    rel.order.place("BUY", 4, order_type=OrderType.MARKET, price=105.0)
    pos = broker.get_positions()[0]
    assert pos.quantity == -6, f"expected -6, got {pos.quantity}"
    assert pos.avg_price == 100.0, f"partial cover keeps entry avg, got {pos.avg_price}"

    # Flatten: BUY 6 @ 110 -> qty 0 -> position removed (must NOT divide by 0).
    rel.order.place("BUY", 6, order_type=OrderType.MARKET, price=110.0)
    assert broker.get_positions() == [], "short must flatten cleanly"
