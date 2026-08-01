"""Tests for Phase A domain types: OrderBook, TradeBook, IVSurface, GreeksTable.

Validates that broker boundary returns proper domain types instead of raw
dicts or DataFrames.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from ntrade.domain.analytics.surface import GreeksTable, IVSurface
from ntrade.domain.instruments.cash import Equity, Index
from ntrade.domain.orders.book import (
    OrderBook,
    OrderBookEntry,
    TradeBook,
    TradeBookEntry,
)
from ntrade.brokers.paper import PaperBroker


# ============================================================ OrderBook
class TestOrderBook:
    def test_empty_orderbook(self):
        ob = OrderBook()
        assert len(ob) == 0
        assert list(ob) == []
        assert ob.to_dicts() == []

    def test_orderbook_with_entries(self):
        entries = (
            OrderBookEntry(symbol="TCS", order_id="1", side="BUY", quantity=100, price=3500.0, status="PENDING"),
            OrderBookEntry(symbol="RELIANCE", order_id="2", side="SELL", quantity=50, price=2400.0, status="PENDING"),
        )
        ob = OrderBook(entries=entries, timestamp=datetime.now())
        assert len(ob) == 2
        assert ob[0].symbol == "TCS"
        assert ob[1].order_id == "2"

    def test_orderbook_for_symbol(self):
        entries = (
            OrderBookEntry(symbol="TCS", order_id="1", side="BUY", quantity=100, price=3500.0, status="PENDING"),
            OrderBookEntry(symbol="RELIANCE", order_id="2", side="SELL", quantity=50, price=2400.0, status="PENDING"),
            OrderBookEntry(symbol="TCS", order_id="3", side="SELL", quantity=25, price=3550.0, status="PENDING"),
        )
        ob = OrderBook(entries=entries)
        tcs = ob.for_symbol("TCS")
        assert len(tcs) == 2
        assert all(e.symbol == "TCS" for e in tcs)

    def test_orderbook_to_dicts(self):
        entries = (
            OrderBookEntry(symbol="TCS", order_id="1", side="BUY", quantity=100, price=3500.0, status="PENDING"),
        )
        ob = OrderBook(entries=entries)
        dicts = ob.to_dicts()
        assert len(dicts) == 1
        assert dicts[0]["symbol"] == "TCS"
        assert dicts[0]["order_id"] == "1"
        assert dicts[0]["side"] == "BUY"

    def test_orderbook_frozen(self):
        ob = OrderBook()
        with pytest.raises(AttributeError):
            ob.entries = ()  # type: ignore

    def test_orderbook_entry_frozen(self):
        entry = OrderBookEntry(symbol="TCS", order_id="1", side="BUY", quantity=100, price=3500.0, status="PENDING")
        with pytest.raises(AttributeError):
            entry.price = 999.0  # type: ignore

    def test_orderbook_entry_to_dict(self):
        entry = OrderBookEntry(symbol="TCS", order_id="1", side="BUY", quantity=100, price=3500.0, status="PENDING")
        d = entry.to_dict()
        assert d["symbol"] == "TCS"
        assert d["quantity"] == 100
        assert d["price"] == 3500.0


# ============================================================ TradeBook
class TestTradeBook:
    def test_empty_tradebook(self):
        tb = TradeBook()
        assert len(tb) == 0
        assert tb.to_dicts() == []

    def test_tradebook_with_entries(self):
        entries = (
            TradeBookEntry(symbol="TCS", trade_id="T1", order_id="1", side="BUY", quantity=100, price=3500.0),
            TradeBookEntry(symbol="RELIANCE", trade_id="T2", order_id="2", side="SELL", quantity=50, price=2400.0),
        )
        tb = TradeBook(entries=entries)
        assert len(tb) == 2
        assert tb[0].trade_id == "T1"

    def test_tradebook_for_symbol(self):
        entries = (
            TradeBookEntry(symbol="TCS", trade_id="T1", order_id="1", side="BUY", quantity=100, price=3500.0),
            TradeBookEntry(symbol="RELIANCE", trade_id="T2", order_id="2", side="SELL", quantity=50, price=2400.0),
        )
        tb = TradeBook(entries=entries)
        assert len(tb.for_symbol("TCS")) == 1
        assert len(tb.for_symbol("UNKNOWN")) == 0

    def test_tradebook_to_dicts(self):
        entries = (
            TradeBookEntry(symbol="TCS", trade_id="T1", order_id="1", side="BUY", quantity=100, price=3500.0),
        )
        tb = TradeBook(entries=entries)
        dicts = tb.to_dicts()
        assert dicts[0]["trade_id"] == "T1"
        assert dicts[0]["price"] == 3500.0


# ============================================================ PaperBroker integration
class TestPaperBrokerBooks:
    def test_paper_returns_orderbook_type(self):
        broker = PaperBroker()
        broker.seed_quote("TCS", ltp=3500.0)
        tcs = Equity("TCS", broker=broker)
        tcs.order.buy(quantity=100, price=3500.0)
        ob = broker.get_orderbook()
        assert isinstance(ob, OrderBook)
        assert len(ob) == 1
        assert ob[0].symbol == "TCS"
        assert ob[0].side == "BUY"

    def test_paper_returns_tradebook_type(self):
        broker = PaperBroker()
        broker.seed_quote("TCS", ltp=3500.0)
        tcs = Equity("TCS", broker=broker)
        tcs.order.buy(quantity=100, price=3500.0)
        tb = broker.get_trade_book()
        assert isinstance(tb, TradeBook)
        assert len(tb) == 1
        assert tb[0].symbol == "TCS"
        assert tb[0].price == 3500.0

    def test_empty_paper_orderbook(self):
        broker = PaperBroker()
        ob = broker.get_orderbook()
        assert isinstance(ob, OrderBook)
        assert len(ob) == 0

    def test_empty_paper_tradebook(self):
        broker = PaperBroker()
        tb = broker.get_trade_book()
        assert isinstance(tb, TradeBook)
        assert len(tb) == 0


# ============================================================ IVSurface
class TestIVSurface:
    def test_from_dataframe(self):
        df = pd.DataFrame([
            {"strike": 100.0, "expiry": "2026-09-25", "type": "CE", "iv": 0.25},
            {"strike": 100.0, "expiry": "2026-09-25", "type": "PE", "iv": 0.28},
        ])
        surface = IVSurface(df)
        assert repr(surface) == "IVSurface(rows=2)"

    def test_to_dataframe(self):
        df = pd.DataFrame([{"strike": 100.0, "iv": 0.25}])
        surface = IVSurface(df)
        result = surface.to_dataframe()
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    def test_delegates_to_dataframe(self):
        df = pd.DataFrame([
            {"strike": 100.0, "iv": 0.25},
            {"strike": 105.0, "iv": 0.30},
        ])
        surface = IVSurface(df)
        # __getattr__ delegation
        assert "iv" in surface.columns
        assert len(surface) == 2
        assert list(surface.columns) == ["strike", "iv"]

    def test_unknown_attr_raises(self):
        surface = IVSurface(pd.DataFrame())
        with pytest.raises(AttributeError, match="IVSurface has no attribute"):
            surface.nonexistent_method()


# ============================================================ GreeksTable
class TestGreeksTable:
    def test_from_dataframe(self):
        df = pd.DataFrame([
            {"strike": 100.0, "type": "CE", "delta": 0.5, "gamma": 0.01, "theta": -0.05, "vega": 0.1, "iv": 0.25},
        ])
        gt = GreeksTable(df)
        assert repr(gt) == "GreeksTable(rows=1)"

    def test_to_dataframe(self):
        df = pd.DataFrame([{"strike": 100.0, "delta": 0.5}])
        gt = GreeksTable(df)
        result = gt.to_dataframe()
        assert isinstance(result, pd.DataFrame)
        assert "delta" in result.columns

    def test_delegates_to_dataframe(self):
        df = pd.DataFrame([
            {"strike": 100.0, "delta": 0.5, "gamma": 0.01},
            {"strike": 105.0, "delta": 0.4, "gamma": 0.02},
        ])
        gt = GreeksTable(df)
        assert "delta" in gt.columns
        assert len(gt) == 2


# ============================================================ OptionChain integration
class TestOptionChainTypedReturns:
    def test_iv_surface_returns_domain_type(self):
        broker = PaperBroker()
        underlying = Index("NIFTY", broker=broker)
        chain = broker.get_option_chain(underlying, num_strikes=5)
        iv = chain.iv_surface()
        assert isinstance(iv, IVSurface)
        assert "iv" in iv.columns

    def test_greeks_table_returns_domain_type(self):
        broker = PaperBroker()
        underlying = Index("NIFTY", broker=broker)
        chain = broker.get_option_chain(underlying, num_strikes=5)
        gt = chain.greeks_table()
        assert isinstance(gt, GreeksTable)
        assert "delta" in gt.columns

    def test_greeks_alias_returns_greeks_table(self):
        broker = PaperBroker()
        underlying = Index("NIFTY", broker=broker)
        chain = broker.get_option_chain(underlying, num_strikes=5)
        g = chain.greeks()
        assert isinstance(g, GreeksTable)
