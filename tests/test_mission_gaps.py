"""Tests for closed mission gaps: corporate actions, option settlement state,
future roll/front/next month links, cover/bracket orders, metadata hydration,
and the (previously dead) signals state. All offline.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.instruments.derivatives import Future, Option
from ntrade.domain.orders.order import OrderType


# ============================================================ corporate actions
def test_corporate_action_record_and_access():
    rel = Equity("RELIANCE")
    rel.record_corporate_action("dividend", amount=9.0, ex_date=date(2026, 9, 1))
    rel.record_corporate_action("split", ratio="1:2")
    actions = rel.corporate_actions
    assert len(actions) == 2
    assert actions[0].action_type == "dividend"
    assert actions[0].amount == 9.0
    assert actions[1].ratio == "1:2"
    assert rel.clear_corporate_actions().corporate_actions == []


def test_corporate_action_immutable_dataclass():
    rel = Equity("RELIANCE")
    rel.record_corporate_action("bonus", ratio="1:1")
    action = rel.corporate_actions[0]
    with pytest.raises(Exception):
        action.amount = 5.0  # frozen dataclass


# ============================================================ option settlement
def test_option_exercise_style_settlement_defaults():
    opt = Option("NIFTY 24400 CE", strike=24400, expiry=date.today(),
                 option_type="CE", underlying_symbol="NIFTY")
    assert opt.exercise_style == "EUROPEAN"
    assert opt.settlement == "CASH"


def test_option_exercise_style_settlement_override():
    opt = Option("TCS 4000 CE", strike=4000, expiry=date.today(),
                 option_type="CE", underlying_symbol="TCS",
                 exercise_style="AMERICAN", settlement="PHYSICAL")
    assert opt.exercise_style == "AMERICAN"
    assert opt.settlement == "PHYSICAL"


# ============================================================ future roll data
def test_future_front_next_month_links():
    spot = Equity("GOLD")
    near = Future("GOLD AUG FUT", underlying="GOLD", expiry=date.today() + timedelta(days=10))
    far = Future("GOLD SEP FUT", underlying="GOLD", expiry=date.today() + timedelta(days=40))
    near.set_next_month(far)
    far.set_front_month(near)
    assert near.next_month is far
    assert far.front_month is near


def test_future_roll_yield_backwardation_positive():
    spot = Equity("GOLD")
    spot._quote = spot._quote.with_update(ltp=70000.0)
    near = Future("GOLD AUG FUT", underlying="GOLD", expiry=date.today() + timedelta(days=10))
    far = Future("GOLD SEP FUT", underlying="GOLD", expiry=date.today() + timedelta(days=40))
    near.set_underlying(spot)
    far.set_underlying(spot)
    near._quote = near._quote.with_update(ltp=70100.0)
    far._quote = far._quote.with_update(ltp=70000.0)
    near.set_next_month(far)
    # backwardation: near > next  -> positive roll yield
    assert near.roll_yield() > 0


def test_future_roll_yield_contango_negative():
    spot = Equity("GOLD")
    near = Future("GOLD AUG FUT", underlying="GOLD", expiry=date.today() + timedelta(days=10))
    far = Future("GOLD SEP FUT", underlying="GOLD", expiry=date.today() + timedelta(days=40))
    near._quote = near._quote.with_update(ltp=70000.0)
    far._quote = far._quote.with_update(ltp=70100.0)
    near.set_next_month(far)
    assert near.roll_yield() < 0


def test_future_roll_yield_zero_without_next():
    fut = Future("SILVER AUG FUT", underlying="SILVER", expiry=date.today() + timedelta(days=10))
    assert fut.roll_yield() == 0.0


# ============================================================ cover / bracket
def test_cover_order_paper():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.cover("SELL", quantity=10, price=2490.0)
    assert order.order_type == OrderType.COVER
    assert order.status.value == "COMPLETED"
    assert order.avg_price == 2490.0


def test_bracket_order_paper_carries_legs():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.bracket("BUY", quantity=75, price=2500.0,
                              target_price=2600.0, stop_loss_price=2450.0)
    assert order.order_type == OrderType.BRACKET
    assert order.target_price == 2600.0
    assert order.stop_loss_price == 2450.0
    assert order.as_dict()["target_price"] == 2600.0


def test_bracket_order_dhan_routes_to_super_order():
    import types
    from ntrade.brokers.dhan import DhanBroker
    seen = {}

    def super_order(**kw):
        seen.update(kw)
        return "SUPER-1"

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(place_super_order=super_order)
    rel = Equity("RELIANCE")
    rel._broker = broker
    order = rel.order.bracket("BUY", quantity=75, price=2500.0,
                              target_price=2600.0, stop_loss_price=2450.0)
    assert order.order_id == "SUPER-1"
    assert order.status.value == "PENDING"
    assert seen["target_price"] == 2600.0
    assert seen["stop_loss_price"] == 2450.0


def test_bracket_order_dhan_rejection_propagates():
    import types
    from ntrade.brokers.dhan import DhanBroker
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(
        place_super_order=lambda **kw: (_ for _ in ()).throw(RuntimeError("margin")))
    rel = Equity("RELIANCE")
    rel._broker = broker
    with pytest.raises(RuntimeError, match="bracket order rejected"):
        rel.order.bracket("BUY", quantity=75, price=2500.0,
                          target_price=2600.0, stop_loss_price=2450.0)


# ============================================================ metadata hydration
def test_hydrate_metadata_from_broker():
    class MetaBroker(PaperBroker):
        def get_instrument_metadata(self, instrument):
            return {"tick_size": 0.05, "lot_size": 1, "freeze_qty": 5000}

    broker = MetaBroker()
    rel = Equity("RELIANCE", broker=broker)
    assert rel.tick_size is None
    rel.hydrate()
    assert rel.tick_size == 0.05
    assert rel.lot_size == 1
    assert rel.freeze_qty == 5000


def test_hydrate_circuit_limits_into_quote():
    class MetaBroker(PaperBroker):
        def get_instrument_metadata(self, instrument):
            return {"circuit_low": 2400.0, "circuit_high": 2600.0}

    broker = MetaBroker()
    rel = Equity("RELIANCE", broker=broker)
    rel.hydrate()
    assert rel._quote.circuit_low == 2400.0
    assert rel._quote.circuit_high == 2600.0


def test_refresh_hydrates_metadata_once():
    calls = {"n": 0}

    class CountingBroker(PaperBroker):
        def get_instrument_metadata(self, instrument):
            calls["n"] += 1
            return {"tick_size": 0.05}

    broker = CountingBroker()
    rel = Equity("RELIANCE", broker=broker)
    rel.refresh()
    rel.refresh()
    assert rel.tick_size == 0.05
    assert calls["n"] == 1  # hydrated exactly once


def test_dhan_get_instrument_metadata_from_file():
    import types
    from ntrade.brokers.dhan import DhanBroker
    from ntrade.brokers.dhan_transport import DhanTransport
    idf = pd.DataFrame([{
        "SEM_TRADING_SYMBOL": "RELIANCE",
        "SEM_CUSTOM_SYMBOL": "RELIANCE",
        "SEM_EXM_EXCH_ID": "NSE",
        "SEM_TICK_SIZE": 0.05, "SEM_LOT_UNITS": 1, "SEM_FREEZE_QTY": 5000,
    }])
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(instrument_df=idf)
    broker._transport = DhanTransport(broker.tsl)
    meta = broker.get_instrument_metadata(Equity("RELIANCE"))
    assert meta == {"tick_size": 0.05, "lot_size": 1, "freeze_qty": 5000}


def test_dhan_get_instrument_metadata_empty_when_unknown():
    import types
    from ntrade.brokers.dhan import DhanBroker
    from ntrade.brokers.dhan_transport import DhanTransport
    idf = pd.DataFrame({
        "SEM_TRADING_SYMBOL": ["SOMETHING"], "SEM_CUSTOM_SYMBOL": ["X"],
        "SEM_EXM_EXCH_ID": ["NSE"], "SM_SYMBOL_NAME": ["SOMETHING"],
        "SEM_INSTRUMENT_NAME": ["FUTCOM"],
    })
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(instrument_df=idf)
    broker._transport = DhanTransport(broker.tsl)
    assert broker.get_instrument_metadata(Equity("RELIANCE")) == {}


def test_base_broker_metadata_default_empty():
    broker = PaperBroker()
    assert broker.get_instrument_metadata(Equity("RELIANCE")) == {}


# ============================================================ signals + download
def test_signals_set_get():
    rel = Equity("RELIANCE")
    rel.set_signal("breakout", True).set_signal("rsi_oversold", True)
    assert rel.signals == {"breakout": True, "rsi_oversold": True}
    assert rel.get_signal("breakout") is True
    assert rel.get_signal("missing", default=None) is None


def test_instrument_download_convenience():
    broker = PaperBroker()
    broker.seed_history("RELIANCE", timeframe="5m")
    rel = Equity("RELIANCE", broker=broker)
    rel._history.download(timeframe="5m")
    assert len(rel._history.df) == 200
