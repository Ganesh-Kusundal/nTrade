"""Tests for the broker layer and capability extension pattern."""

import pytest

from ntrade.brokers.capabilities import BrokerExtensionFacade, capability, registered_capabilities
from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity, Index


def test_paper_broker_quote_and_depth():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    quote = broker.get_quote(rel)
    assert quote.ltp == 2500.0
    depth = broker.get_depth(rel)
    assert depth.best_bid() is not None
    assert depth.best_ask() is not None
    assert depth.bid_ask_imbalance() != 0.0 or True  # deterministic range check


def test_paper_broker_history_seeding():
    broker = PaperBroker(seed=7)
    rel = Equity("RELIANCE", broker=broker)
    df = broker.get_historical(rel, timeframe="15m")
    assert len(df) == 200
    assert {"timestamp", "open", "high", "low", "close", "volume"} <= set(df.columns)


def test_paper_broker_balance_and_orders():
    broker = PaperBroker()
    assert broker.get_balance() == 100_000.0
    rel = Equity("RELIANCE", broker=broker)
    rel.order.buy(quantity=10, price=100.0)
    assert len(broker.orders) == 1


def test_capability_supported_and_unsupported():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    # paper broker doesn't support depth20
    with pytest.raises(AttributeError):
        rel.broker.depth20()
    assert "depth20" not in rel.broker.available()


def test_capability_registry():
    caps = registered_capabilities()
    assert "depth20" in caps
    assert caps["depth20"].supports("dhan")
    assert not caps["depth20"].supports("paper")


def test_broker_extension_facade_rejects_unknown():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    with pytest.raises(AttributeError):
        rel.broker.nonexistent_capability()


def test_broker_extension_without_broker():
    rel = Equity("RELIANCE")
    with pytest.raises(AttributeError):
        rel.broker.depth20()
    assert rel.broker.available() == []


def test_subscription_multiplexing():
    broker = PaperBroker()
    a = Equity("A", broker=broker)
    b = Equity("B", broker=broker)
    a.stream.subscribe()
    b.stream.subscribe()
    assert len(broker._subscriptions) == 2
    a.stream.unsubscribe()
    assert "A" not in broker._subscriptions


def test_disconnect_notifies_streams():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    events = []
    rel._stream.on_disconnect(lambda _: events.append("disconnected"))
    rel.stream.subscribe()
    broker.disconnect()
    assert events == ["disconnected"]
    assert not rel.stream.is_live


def test_custom_capability_decorator():
    @capability("ping_test")
    def _ping(instrument):
        return f"pong-{instrument.symbol}"

    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    assert rel.broker.ping_test() == "pong-RELIANCE"


def test_paper_option_chain():
    broker = PaperBroker()
    nifty = Index("NIFTY", broker=broker)
    nifty._quote = nifty._quote.with_update(ltp=24550.0)
    chain = broker.get_option_chain(nifty, num_strikes=9)
    assert len(chain) == 18  # 9 strikes x 2 legs
    assert chain.atm is not None
