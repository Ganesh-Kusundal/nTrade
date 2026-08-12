"""PaperBroker must report authoritative balance + positions from its fills
(so PositionSyncEngine reconciles paper to its own reality instead of wiping
the kernel). Regression for the paper/synth position-sync wipe bug."""

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.orders.order import OrderType


def test_paper_broker_reports_authoritative_balance_and_positions():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    broker.seed_quote("RELIANCE", 100.0)

    # Buy 10 @ 100 -> balance decreases; a position appears.
    rel.order.place("BUY", 10, order_type=OrderType.MARKET)
    assert broker.get_balance() < 100_000.0, "balance must decrease on a buy fill"
    pos = broker.get_positions()
    assert len(pos) == 1 and pos[0].quantity == 10, "position must be tracked"

    # Sell 10 @ 101 -> back to flat; balance increases by the sell notional.
    rel.order.place("SELL", 10, order_type=OrderType.MARKET, price=101.0)
    assert broker.get_positions() == [], "position must close on the sell"
    assert broker.get_balance() > 99_900.0, "balance must recover on the sell"
