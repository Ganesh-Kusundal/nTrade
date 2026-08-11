"""Tests for TradingSession — the unified SDK entry point."""

from __future__ import annotations

from datetime import date

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity, Index
from ntrade.domain.instruments.derivatives import Future, Option
from ntrade.engines.strategy_engine import Strategy
from ntrade.events.market import QuoteEvent
from ntrade.kernel.session import TradingKernel
from ntrade.kernel.trading_session import TradingSession


# ================================================================ construction

class TestTradingSessionConstructors:
    def test_paper_creates_session(self):
        session = TradingSession.paper()
        assert session.mode == "paper"
        assert session.broker is not None
        assert isinstance(session.broker, PaperBroker)
        assert session.kernel is not None
        assert isinstance(session.kernel, TradingKernel)

    def test_paper_custom_cash(self):
        session = TradingSession.paper(initial_cash=500_000.0)
        assert session.balance() == 500_000.0

    def test_connect_with_broker_name(self):
        session = TradingSession.connect("paper")
        assert session.mode == "live"
        assert session.broker is not None
        assert session.broker.name == "paper"

    def test_connect_unknown_broker_raises(self):
        with pytest.raises(KeyError, match="No broker registered"):
            TradingSession.connect("nonexistent_broker")

    def test_replay_creates_session(self):
        from datetime import datetime
        events = [QuoteEvent(symbol="X", exchange="NSE", ts=datetime(2026, 1, 1))]
        session = TradingSession.replay(events)
        assert session.mode == "replay"
        assert hasattr(session, "_replay_events")

    def test_explicit_broker(self):
        broker = PaperBroker()
        session = TradingSession(broker=broker, mode="paper")
        assert session.broker is broker

    def test_no_broker(self):
        session = TradingSession(mode="sim")
        assert session.broker is None
        assert session.connected is False


# ============================================================ instrument creation

class TestTradingSessionInstruments:
    def test_stock_returns_equity(self):
        session = TradingSession.paper()
        tcs = session.stock("TCS")
        assert isinstance(tcs, Equity)
        assert tcs.symbol == "TCS"

    def test_index_returns_index(self):
        session = TradingSession.paper()
        nifty = session.index("NIFTY")
        assert isinstance(nifty, Index)
        assert nifty.symbol == "NIFTY"

    def test_etf(self):
        session = TradingSession.paper()
        etf = session.etf("NIFTYBEES")
        assert etf.symbol == "NIFTYBEES"

    def test_commodity(self):
        session = TradingSession.paper()
        gold = session.commodity("GOLD")
        assert gold.symbol == "GOLD"

    def test_currency(self):
        session = TradingSession.paper()
        usd = session.currency("USDINR")
        assert usd.symbol == "USDINR"

    def test_future(self):
        session = TradingSession.paper()
        nifty = session.index("NIFTY")
        fut = session.future(nifty, date(2026, 8, 28))
        assert isinstance(fut, Future)
        assert fut.exchange == "NFO"

    def test_future_mcx_from_commodity(self):
        session = TradingSession.paper()
        crude = session.commodity("CRUDEOIL")
        fut = session.future(crude, date(2026, 8, 19))
        assert isinstance(fut, Future)
        assert fut.exchange == "MCX"
        assert fut.underlying_symbol == "CRUDEOIL"

    def test_option(self):
        session = TradingSession.paper()
        nifty = session.index("NIFTY")
        opt = session.option(nifty, 24500, date(2026, 8, 6), "CE")
        assert isinstance(opt, Option)
        assert opt.strike == 24500
        assert opt.option_type == "CE"


# ============================================================ account / portfolio

class TestTradingSessionAccount:
    def test_balance_with_paper_broker(self):
        session = TradingSession.paper(initial_cash=200_000.0)
        assert session.balance() == 200_000.0

    def test_balance_without_broker(self):
        session = TradingSession(mode="sim")
        session.kernel.ctx.account.balance = 75_000.0
        assert session.balance() == 75_000.0

    def test_account_returns_composite(self):
        session = TradingSession.paper()
        acct = session.account()
        assert hasattr(acct, "balance")

    def test_portfolio_returns_composite(self):
        session = TradingSession.paper()
        pf = session.portfolio()
        assert hasattr(pf, "positions")

    def test_positions_with_paper_broker(self):
        session = TradingSession.paper()
        positions = session.positions()
        assert isinstance(positions, (list, dict))


# ============================================================ engine stack

class TestTradingSessionEngine:
    def test_register_instrument(self):
        session = TradingSession.paper()
        nifty = session.index("NIFTY")
        result = session.register(nifty)
        assert result is session
        assert session.kernel.ctx.instrument("NIFTY") is nifty

    def test_register_strategy(self):
        session = TradingSession.paper()

        class DummyStrategy(Strategy):
            def on_bar(self, instrument):
                pass

        strat = DummyStrategy()
        name = session.register_strategy(strat)
        assert name in session.runner.names()

    def test_start_and_stop(self):
        session = TradingSession.paper()
        result = session.start()
        assert result is session
        result = session.stop(reason="test")
        assert result is session

    def test_chaining(self):
        session = TradingSession.paper()
        nifty = session.index("NIFTY")
        result = session.register(nifty).start().stop(reason="chain test")
        assert result is session


# ============================================================ accessors

class TestTradingSessionAccessors:
    def test_kernel_accessor(self):
        session = TradingSession.paper()
        assert isinstance(session.kernel, TradingKernel)

    def test_runner_accessor(self):
        session = TradingSession.paper()
        assert session.runner is not None

    def test_mode_accessor(self):
        session = TradingSession.paper()
        assert session.mode == "paper"

    def test_repr(self):
        session = TradingSession.paper()
        r = repr(session)
        assert "TradingSession" in r
        assert "paper" in r

    def test_connect_disconnect(self):
        session = TradingSession.paper()
        session.connect_broker()
        assert session.connected is True
        session.disconnect()


# ============================================================ replay

class TestTradingSessionReplay:
    def test_replay_feeds_events(self):
        from ntrade.kernel.clock import ReplayClock
        from ntrade.events.market import TickEvent
        from datetime import datetime

        events = [
            TickEvent(symbol="NIFTY", exchange="NSE", price=24500.0,
                      ts=datetime(2026, 1, 1, 9, 15)),
        ]
        session = TradingSession.replay(events, timeframe="1m")
        session.start()
        # After replay, the kernel should have processed the events
        assert session.kernel.bus.history is not None
