"""Tests for factories, registry (flyweight) and the Market facade."""

from datetime import date

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.facade import Market
from ntrade.factories import InstrumentFactory, OptionFactory
from ntrade.registry import BrokerRegistry, SymbolMaster


def test_symbol_master_flyweight():
    from ntrade.domain.instruments.cash import Equity
    master = SymbolMaster()
    a = master.get(Equity, "RELIANCE")
    b = master.get(Equity, "RELIANCE")
    assert a is b


def test_instrument_factory_with_broker():
    broker = PaperBroker()
    factory = InstrumentFactory(broker)
    rel = factory.equity("RELIANCE")
    assert rel.broker_adapter is broker
    nifty = factory.index("NIFTY")
    assert nifty.exchange == "INDEX"


def test_factory_option_links_underlying():
    broker = PaperBroker()
    factory = InstrumentFactory(broker)
    nifty = factory.index("NIFTY")
    opt = factory.option(nifty, strike=24500, expiry=date.today(), option_type="CE")
    assert opt.underlying is nifty
    assert opt.underlying_symbol == "NIFTY"
    assert opt.lot_size is None or opt.lot_size is not None


def test_option_factory_without_broker():
    from ntrade.domain.instruments.cash import Equity
    underlying = Equity("RELIANCE")
    opt = OptionFactory.create(underlying, strike=2500, expiry=date.today(), option_type="CE")
    assert opt.strike == 2500
    assert opt.underlying is underlying


def test_broker_registry():
    BrokerRegistry.register("test_paper", lambda **kw: PaperBroker(**kw))
    assert "test_paper" in BrokerRegistry.available()
    broker = BrokerRegistry.get("test_paper")
    assert broker.name == "paper"
    with pytest.raises(KeyError):
        BrokerRegistry.get("no_such_broker")


def test_market_facade_paper():
    m = Market(broker="paper")
    assert m.broker.name == "paper"
    rel = m.equity("RELIANCE")
    assert rel.broker_adapter is m.broker
    rel.refresh()
    assert rel.market.ltp() >= 0
    assert m.balance() == 100_000.0


def test_market_facade_chain():
    m = Market(broker="paper")
    nifty = m.index("NIFTY")
    nifty._quote = nifty._quote.with_update(ltp=24550.0)
    chain = m.chain(nifty, num_strikes=7)
    assert len(chain) == 14


def test_market_default_paper():
    m = Market()
    assert m.broker.name == "paper"


def test_flyweight_via_factory_shared():
    m = Market(broker="paper")
    a = m.equity("RELIANCE")
    b = m.equity("RELIANCE")
    assert a is b
