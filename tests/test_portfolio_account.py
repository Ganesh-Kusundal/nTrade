"""Tests for the Portfolio/Account composite domain objects."""

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.portfolio import Account, Holding, Portfolio, Position


def test_position_pnl_and_market_value():
    p = Position(symbol="RELIANCE", quantity=10, avg_price=100.0, ltp=110.0)
    assert p.pnl == 100.0
    assert p.market_value == 1100.0


def test_portfolio_contains_positions():
    port = Portfolio(positions=[
        Position(symbol="RELIANCE", quantity=10, avg_price=100.0, ltp=110.0),
        Position(symbol="TCS", quantity=5, avg_price=50.0, ltp=40.0),
    ])
    assert len(port) == 2
    assert port.pnl == 100.0 + (-50.0)
    assert port.market_value == 1100.0 + 200.0
    assert port["RELIANCE"].quantity == 10
    with pytest.raises(KeyError):
        port["NOPE"]


def test_account_from_paper_broker():
    broker = PaperBroker()
    acct = Account.from_broker(broker)
    assert acct.balance == 100_000.0
    assert len(acct) == 0


def test_account_holding_lookup():
    acct = Account(balance=500.0, holdings=[Holding(symbol="RELIANCE", quantity=2, avg_price=100.0, ltp=120.0)])
    h = acct.holding("RELIANCE")
    assert h.pnl == 40.0
    assert acct.holding("NOPE") is None


def test_portfolio_from_broker_empty():
    broker = PaperBroker()
    port = Portfolio.from_broker(broker)
    assert len(port) == 0
    assert port.pnl == 0.0
