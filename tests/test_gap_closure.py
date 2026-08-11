"""Tests for the closed endpoint gaps (order lifecycle, books, risk, market data,
advanced orders, analytics) across Paper and Dhan brokers.

Every test is offline: Dhan is stubbed with a SimpleNamespace Tradehull.
"""

import types
from datetime import date

import pandas as pd
import pytest

from ntrade.brokers.dhan import DhanBroker
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity, Index
from ntrade.domain.instruments.derivatives import Option
from ntrade.domain.orders.order import Order, OrderSide, OrderStatus, OrderType, TradeType
from ntrade.domain.portfolio import Portfolio


def make_broker(**tsl_methods):
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(**tsl_methods)
    broker._transport = DhanTransport(broker.tsl)
    return broker


def _dhan_order(order_id="ORD-1"):
    broker = make_broker(
        cancel_order=lambda **kw: None,
        modify_order=lambda **kw: None,
        get_order_status=lambda orderid: "TRADED",
        get_order_detail=lambda orderid, debug="NO": {
            "orderId": order_id, "orderStatus": "TRADED",
            "filledQty": 75, "avgPrice": 100.0,
        },
    )
    rel = Equity("RELIANCE")
    rel._broker = broker
    rel._quote = rel._quote.with_update(ltp=100.0)
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=75,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=100.0,
                  order_id=order_id, status=OrderStatus.PENDING)
    return broker, rel, order


# ============================================================ Phase A: lifecycle
def test_order_cancel_paper():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=100.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=10, price=100.0)
    order.cancel()
    assert order.status == OrderStatus.CANCELLED
    assert broker.get_order_detail(order.order_id)["status"] == "CANCELLED"


def test_order_modify_paper():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=100.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=10, price=100.0)
    order.modify(price=105.0, quantity=20)
    assert order.price == 105.0
    assert order.quantity == 20
    assert broker.get_order_detail(order.order_id)["price"] == 105.0


def test_order_refresh_paper():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=100.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=10, price=100.0)
    order.refresh()
    assert order.status == OrderStatus.COMPLETED


def test_order_cancel_without_broker_raises():
    rel = Equity("RELIANCE")
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=1)
    with pytest.raises(RuntimeError):
        order.cancel()


def test_order_cancel_dhan():
    broker, rel, order = _dhan_order()
    order.cancel()
    assert order.status == OrderStatus.CANCELLED


def test_order_refresh_dhan_maps_status_and_fills():
    broker, rel, order = _dhan_order()
    order.refresh()
    assert order.status == OrderStatus.COMPLETED
    assert order.filled_qty == 75
    assert order.avg_price == 100.0


def test_order_modify_dhan_passes_params():
    seen = {}

    def modify(**kw):
        seen.update(kw)
        return None

    broker, rel, order = _dhan_order()
    broker.tsl.modify_order = modify
    order.modify(price=99.0, quantity=50)
    assert seen["order_id"] == "ORD-1"
    assert seen["price"] == 99.0
    assert seen["quantity"] == 50
    assert order.price == 99.0
    assert order.quantity == 50


def test_get_order_detail_normalizes():
    broker, rel, order = _dhan_order()
    detail = broker.get_order_detail("ORD-1")
    assert detail["order_id"] == "ORD-1"
    assert detail["filled_qty"] == 75
    assert detail["avg_price"] == 100.0
    assert detail["status"] == "TRADED"


# ============================================================ Phase B: books
def test_paper_orderbook_and_tradebook():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=100.0)
    rel = Equity("RELIANCE", broker=broker)
    rel.order.buy(quantity=10, price=100.0)
    rel.order.sell(quantity=5, price=101.0)
    assert len(broker.get_orderbook()) == 2
    assert len(broker.get_trade_book()) == 2  # both filled on paper
    report = broker.order_report()
    assert report["orders"] == 2


def test_facade_books_work_on_paper():
    from ntrade.facade import Market
    m = Market(broker="paper")
    rel = m.equity("RELIANCE")
    rel.order.buy(quantity=10, price=100.0)
    assert len(m.orderbook()) == 1
    assert len(m.tradebook()) == 1
    assert m.order_report()["orders"] == 1


def test_dhan_orderbook_passthrough():
    broker = make_broker(get_orderbook=lambda debug="NO": [{"orderId": "1"}],
                         get_trade_book=lambda debug="NO": [{"tradeId": "1"}],
                         order_report=lambda: {"x": 1})
    ob = broker.get_orderbook()
    assert len(ob) == 1
    assert ob[0].order_id == "1"
    tb = broker.get_trade_book()
    assert len(tb) == 1
    assert tb[0].trade_id == "1"
    assert broker.order_report() == {"x": 1}


def test_dhan_orderbook_dataframe_normalized():
    """Dhan's books return DataFrames — must be normalized to domain types."""
    df = pd.DataFrame([{"orderId": "1", "status": "TRADED"},
                       {"orderId": "2", "status": "PENDING"}])
    broker = make_broker(get_orderbook=lambda debug="NO": df,
                         get_trade_book=lambda debug="NO": df,
                         order_report=lambda: (df, df, df))
    ob = broker.get_orderbook()
    assert len(ob) == 2
    assert ob[0].order_id == "1"
    assert ob[0].status == "TRADED"
    assert ob[1].order_id == "2"
    assert ob[1].status == "PENDING"
    assert len(broker.get_trade_book()) == 2
    report = broker.order_report()
    # order_report still returns raw dicts (not typed) — only books are typed
    assert len(report["orders"]) == 2
    assert len(report["positions"]) == 2
    assert len(report["trades"]) == 2


def test_to_records_symbol_keyed_dict_keeps_keys():
    """Symbol-keyed dicts must keep their key merged into each row."""
    from ntrade.brokers.dhan_mapper import to_records
    rows = to_records({"RELIANCE": {"qty": 10, "ltp": 100.0},
                       "TCS": [{"qty": 5, "ltp": 50.0}, {"qty": 2, "ltp": 51.0}]})
    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[1]["symbol"] == "TCS"
    assert rows[2]["symbol"] == "TCS"
    assert rows[2]["qty"] == 2


def test_start_date_and_instrument_file_capabilities():
    from ntrade.brokers.capabilities import registered_capabilities
    broker = make_broker(get_start_date=lambda: "2026-08-01",
                         get_instrument_file=lambda: "/tmp/instruments.csv")
    rel = Equity("RELIANCE")
    rel._broker = broker
    assert rel.broker.start_date() == "2026-08-01"
    assert rel.broker.instrument_file() == "/tmp/instruments.csv"
    assert registered_capabilities()["start_date"].supports("dhan")
    assert registered_capabilities()["instrument_file"].supports("dhan")


# ============================================================ Phase C: risk
def test_portfolio_live_pnl_from_broker():
    broker = make_broker(get_live_pnl=lambda: 1234.5,
                         get_positions=lambda: pd.DataFrame(),
                         get_holdings=lambda: pd.DataFrame())
    port = Portfolio.from_broker(broker)
    assert port.live_pnl == 1234.5


def test_portfolio_live_pnl_falls_back_to_calculated():
    broker = PaperBroker()
    port = Portfolio.from_broker(broker)
    assert port.live_pnl == 0.0


def test_facade_live_pnl():
    from ntrade.facade import Market
    m = Market(broker="paper")
    assert m.live_pnl() == 0.0


def test_margin_calculator_capability():
    from ntrade.brokers.capabilities import registered_capabilities
    cap = registered_capabilities()["margin_calculator"]
    assert cap.supports("dhan")
    broker = make_broker(margin_calculator=lambda **kw: {"margin": 5000})
    rel = Equity("RELIANCE")
    rel._broker = broker
    result = rel.broker.margin_calculator(quantity=75, transaction_type="BUY")
    assert result == {"margin": 5000}


def test_kill_switch_capability():
    from ntrade.brokers.capabilities import registered_capabilities
    broker = make_broker(kill_switch=lambda action: f"killed:{action}")
    rel = Equity("RELIANCE")
    rel._broker = broker
    assert rel.broker.kill_switch(action="DEACTIVATE") == "killed:DEACTIVATE"


def test_enable_pnl_based_exit_capability():
    from ntrade.brokers.capabilities import registered_capabilities
    broker = make_broker(enable_pnl_based_exit=lambda **kw: "enabled")
    rel = Equity("RELIANCE")
    rel._broker = broker
    assert rel.broker.enable_pnl_based_exit(profit_value=1000, loss_value=500) == "enabled"


def test_capabilities_not_supported_on_paper():
    from ntrade.brokers.capabilities import BrokerExtensionFacade
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    facade = BrokerExtensionFacade(rel)
    assert "margin_calculator" not in facade.available()
    with pytest.raises(AttributeError):
        rel.broker.margin_calculator(quantity=1, transaction_type="BUY")


# ============================================================ Phase D: market data
def test_get_expiry_list_returns_dates():
    broker = make_broker(get_expiry_list=lambda **kw: ["2026-08-06", "2026-08-13"])
    nifty = Index("NIFTY")
    dates = broker.get_expiry_list(nifty)
    assert dates == [date(2026, 8, 6), date(2026, 8, 13)]


def test_chain_resolves_real_expiry():
    rows = []
    for strike in (24400, 24450):
        for leg in ("CE", "PE"):
            rows.append({
                "Strike Price": strike,
                f"{leg} LTP": 50.0, f"{leg} OI": 10000, f"{leg} Volume": 100,
                f"{leg} IV": 0.15, f"{leg} Delta": 0.5, f"{leg} Gamma": 0.001,
                f"{leg} Theta": -0.2, f"{leg} Vega": 0.1,
            })
    chain_df = pd.DataFrame(rows)
    broker = make_broker(
        get_option_chain=lambda **kw: (24450, chain_df),
        get_expiry_list=lambda **kw: ["2026-08-06", "2026-08-13"],
    )
    nifty = Index("NIFTY")
    chain = broker.get_option_chain(nifty, expiry=0, num_strikes=2)
    assert chain.target_expiry == date(2026, 8, 6)          # real expiry, not today
    assert chain.expiry_list == [date(2026, 8, 6), date(2026, 8, 13)]
    assert all(o.expiry == date(2026, 8, 6) for o in chain)


def test_chain_expiry_placeholder_when_list_missing():
    """When get_expiry_list is unavailable, chain keeps placeholder state."""
    rows = []
    for strike in (24400, 24450):
        for leg in ("CE", "PE"):
            rows.append({"Strike Price": strike, f"{leg} LTP": 50.0, f"{leg} OI": 10000,
                         f"{leg} Volume": 100, f"{leg} IV": 0.15, f"{leg} Delta": 0.5,
                         f"{leg} Gamma": 0.001, f"{leg} Theta": -0.2, f"{leg} Vega": 0.1})
    chain_df = pd.DataFrame(rows)
    broker = make_broker(get_option_chain=lambda **kw: (24450, chain_df))
    nifty = Index("NIFTY")
    chain = broker.get_option_chain(nifty, expiry=0, num_strikes=2)
    assert chain.expiry_list == []           # no list resolved
    assert chain.target_expiry is None              # placeholder untouched
    assert chain.expiry_index_used == 0      # no fallback needed


def test_get_lot_size():
    broker = make_broker(get_lot_size=lambda tradingsymbol: "75")
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400, expiry=date(2026, 8, 6),
                 option_type="CE", underlying_symbol="NIFTY")
    assert broker.get_lot_size(opt) == 75


def test_get_long_term_historical():
    raw = pd.DataFrame({
        "Timestamp": ["2026-01-02", "2026-01-05"],
        "Open": [100, 101], "High": [102, 103], "Low": [99, 100],
        "Close": [101, 102], "Volume": [1000, 2000],
    })
    broker = make_broker(get_long_term_historical_data=lambda **kw: raw)
    nifty = Index("NIFTY")
    df = broker.get_long_term_historical(nifty, timeframe="1d", from_date="2026-01-01", to_date="2026-01-31")
    assert "close" in df.columns
    assert len(df) == 2


def test_get_ohlc():
    broker = make_broker(get_ohlc_data=lambda names: {names[0]: {"open": 100, "high": 105}})
    rel = Equity("RELIANCE")
    ohlc = broker.get_ohlc(rel)
    assert ohlc["high"] == 105


def test_future_script_and_start_date():
    broker = make_broker(get_future_script=lambda **kw: "NIFTY 26 AUG 2026",
                         get_start_date=lambda: "2026-08-01")
    nifty = Index("NIFTY")
    assert broker.get_future_script(nifty, expiry=1) == "NIFTY 26 AUG 2026"
    assert broker.get_start_date() == "2026-08-01"


def test_lot_size_capability():
    from ntrade.brokers.capabilities import registered_capabilities
    broker = make_broker(get_lot_size=lambda tradingsymbol: 75)
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400, expiry=date(2026, 8, 6),
                 option_type="CE", underlying_symbol="NIFTY")
    opt._broker = broker
    assert opt.broker.lot_size() == 75
    assert registered_capabilities()["lot_size"].supports("dhan")


# ============================================================ Phase E: advanced orders
def test_super_order_capability():
    from ntrade.brokers.capabilities import registered_capabilities
    broker = make_broker(place_super_order=lambda **kw: "SUPER-1")
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400, expiry=date(2026, 8, 6),
                 option_type="CE", underlying_symbol="NIFTY")
    opt._broker = broker
    order_id = opt.broker.place_super_order(
        side="BUY", quantity=75, price=100, target_price=110, stop_loss_price=95)
    assert order_id == "SUPER-1"


def test_slice_order_capability():
    broker = make_broker(place_slice_order=lambda **kw: "SLICE-1")
    rel = Equity("RELIANCE")
    rel._broker = broker
    assert rel.broker.place_slice_order(side="SELL", quantity=100, price=99) == "SLICE-1"


def test_conditional_trigger_capability():
    broker = make_broker(place_conditional_trigger=lambda **kw: "TRIG-1")
    rel = Equity("RELIANCE")
    rel._broker = broker
    oid = rel.broker.place_conditional_trigger(
        side="SELL", quantity=10, price=90, trigger_price=95)
    assert oid == "TRIG-1"


def test_forever_order_capability():
    broker = make_broker(place_forever_order=lambda **kw: "GTT-1")
    rel = Equity("RELIANCE")
    rel._broker = broker
    assert rel.broker.place_forever_order(side="BUY", quantity=10, price=100) == "GTT-1"


def test_cancel_all_orders_capability():
    broker = make_broker(cancel_all_orders=lambda: {"cancelled": 3})
    rel = Equity("RELIANCE")
    rel._broker = broker
    assert rel.broker.cancel_all_orders() == {"cancelled": 3}


# ============================================================ Phase F: analytics
def test_heikin_ashi_transform():
    df = pd.DataFrame({
        "open": [100, 102], "high": [103, 105], "low": [99, 101], "close": [102, 104],
        "volume": [1000, 2000],
    })
    from ntrade.domain.analytics.indicators import heikin_ashi
    ha = heikin_ashi(df)
    assert "close" in ha.columns
    assert ha["close"].iloc[0] == (100 + 103 + 99 + 102) / 4
    assert ha["open"].iloc[0] == (100 + 102) / 2


def test_renko_bricks():
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-01", periods=6, freq="5min"),
        "close": [100, 108, 115, 106, 98, 90],
    })
    from ntrade.domain.analytics.indicators import renko_bricks
    bricks = renko_bricks(df, box_size=7)
    assert not bricks.empty
    assert set(bricks.columns) >= {"close", "direction"}


def test_instrument_heikin_ashi_and_renko():
    broker = PaperBroker()
    broker.seed_history("RELIANCE", rows=50)
    rel = Equity("RELIANCE", broker=broker)
    rel._history.fetch(force=True)
    ha = rel.analytics.heikin_ashi()
    assert not ha.empty
    assert "close" in ha.columns
    bricks = rel.analytics.renko(box_size=2)
    assert isinstance(bricks, pd.DataFrame)


def test_order_executed_price_and_time_dhan():
    broker, rel, order = _dhan_order()
    broker.tsl.get_executed_price = lambda orderid, debug="NO": 99.5
    broker.tsl.get_executed_price_and_time = lambda orderid, debug="NO": (99.5, "2026-08-01 10:00:00")
    assert order.executed_price() == 99.5
    price, ts = order.executed_price_and_time()
    assert price == 99.5
    assert "2026-08-01" in ts


def test_order_executed_price_paper():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=100.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=10, price=100.0)
    assert order.executed_price() == 100.0


# ------------------------------------------------- K-020: no silent 0.0 on DH-904
def test_executed_price_propagates_rate_limited():
    """A DH-904 on the executed-price poll must raise RateLimited, never look
    like a fill at 0.0 (K-020: no-silent-swallow gap closed)."""
    from ntrade.execution.rate_limit import Quota, RateLimited
    broker = make_broker(
        get_executed_price=lambda orderid, debug="NO": (_ for _ in ()).throw(
            RateLimited(Quota.ORDER)),
    )
    rel = Equity("RELIANCE")
    rel._broker = broker
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=75,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=100.0,
                  order_id="ORD-1", status=OrderStatus.PENDING)
    with pytest.raises(RateLimited):
        order.executed_price()


def test_executed_price_and_time_propagates_rate_limited():
    """get_executed_price_and_time must also re-raise RateLimited (not return
    (0.0, "") which looks like a fill at zero with no exchange time)."""
    from ntrade.execution.rate_limit import Quota, RateLimited
    broker = make_broker(
        get_executed_price_and_time=lambda orderid, debug="NO": (_ for _ in ()).throw(
            RateLimited(Quota.ORDER)),
    )
    rel = Equity("RELIANCE")
    rel._broker = broker
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=75,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=100.0,
                  order_id="ORD-1", status=OrderStatus.PENDING)
    with pytest.raises(RateLimited):
        order.executed_price_and_time()


def test_executed_price_propagates_other_errors():
    """Non-rate transport errors propagate (H-5): a silent fallback to the
    order's recorded avg_price would mask a dead broker and feed stale prices
    into PnL (B-005). Only RateLimited is treated as quota backoff."""
    from unittest.mock import MagicMock
    broker = make_broker()
    broker._transport = MagicMock()
    broker._transport.get_executed_price.side_effect = ConnectionError("network down")
    rel = Equity("RELIANCE")
    rel._broker = broker
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=75,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=100.0,
                  order_id="ORD-1", status=OrderStatus.PENDING, avg_price=99.5)
    with pytest.raises(RuntimeError, match="executed price fetch failed"):
        order.executed_price()


def test_executed_price_and_time_propagates_other_errors():
    """Same no-swallow contract for the price+time pair: propagate the error
    rather than returning (avg_price, "")."""
    from unittest.mock import MagicMock
    broker = make_broker()
    broker._transport = MagicMock()
    broker._transport.get_executed_price_and_time.side_effect = ConnectionError("network down")
    rel = Equity("RELIANCE")
    rel._broker = broker
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=75,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=100.0,
                  order_id="ORD-1", status=OrderStatus.PENDING, avg_price=99.5)
    with pytest.raises(RuntimeError, match="executed price/time fetch failed"):
        order.executed_price_and_time()


def test_super_order_management_capabilities():
    from ntrade.brokers.capabilities import registered_capabilities
    broker = make_broker(
        get_super_orders=lambda: [{"orderId": "S1"}],
        modify_super_order=lambda **kw: "MOD-S1",
        cancel_super_order=lambda **kw: "CXL-S1",
        get_forever_orders=lambda: [{"orderId": "F1"}],
        modify_forever_order=lambda **kw: "MOD-F1",
        cancel_forever_order=lambda order_id: f"CXL-{order_id}",
        get_all_conditional_triggers=lambda timeout=10: [{"alertId": "A1"}],
        get_conditional_trigger_by_id=lambda alert_id, timeout=10: {"alertId": alert_id},
        delete_conditional_trigger=lambda alert_id, timeout=10: "DEL-A1",
    )
    rel = Equity("RELIANCE")
    rel._broker = broker
    assert rel.broker.get_super_orders() == [{"orderId": "S1"}]
    assert rel.broker.modify_super_order(order_id="S1", leg_name="TARGET_LEG",
                                         quantity=50, order_type="LIMIT", price=110) == "MOD-S1"
    assert rel.broker.cancel_super_order(order_id="S1") == "CXL-S1"
    assert rel.broker.get_forever_orders() == [{"orderId": "F1"}]
    assert rel.broker.modify_forever_order(order_id="F1", order_flag="SINGLE",
                                           order_type="LIMIT", quantity=10, price=100) == "MOD-F1"
    assert rel.broker.cancel_forever_order(order_id="F1") == "CXL-F1"
    assert rel.broker.get_conditional_triggers() == [{"alertId": "A1"}]
    assert rel.broker.get_conditional_trigger(alert_id="A1") == {"alertId": "A1"}
    assert rel.broker.delete_conditional_trigger(alert_id="A1") == "DEL-A1"
    for name in ("get_super_orders", "modify_super_order", "cancel_super_order",
                 "get_forever_orders", "modify_forever_order", "cancel_forever_order",
                 "get_conditional_triggers", "get_conditional_trigger",
                 "delete_conditional_trigger"):
        assert registered_capabilities()[name].supports("dhan")


def test_strike_selection_capabilities():
    broker = make_broker(
        ATM_Strike_Selection=lambda **kw: ("ATM-CE", "ATM-PE", 24450),
        ITM_Strike_Selection=lambda **kw: ("ITM-CE",),  # noqa: E501
        OTM_Strike_Selection=lambda **kw: ("OTM-CE",),
        get_expired_option_data=lambda **kw: pd.DataFrame([{"timestamp": "2026-08-01"}]),
        get_exchange_time=lambda orderid, debug="NO": "2026-08-01 10:00:00",
    )
    nifty = Index("NIFTY")
    nifty._broker = broker
    assert nifty.broker.atm_strike(expiry=0) == ("ATM-CE", "ATM-PE", 24450)
    assert nifty.broker.itm_strike(expiry=0, count=2) == ("ITM-CE",)
    assert nifty.broker.otm_strike(expiry=0) == ("OTM-CE",)
    assert len(nifty.broker.expired_option_data(interval=5, expiry_flag="FUT", expiry_code=1)) == 1
    assert nifty.broker.exchange_time(orderid="ORD-1") == "2026-08-01 10:00:00"


def test_option_chain_expiries_real():
    """chain.expiries returns the real expiry (not date.today()) after resolution."""
    rows = []
    for strike in (24400, 24450):
        for leg in ("CE", "PE"):
            rows.append({
                "Strike Price": strike,
                f"{leg} LTP": 50.0, f"{leg} OI": 10000, f"{leg} Volume": 100,
                f"{leg} IV": 0.15, f"{leg} Delta": 0.5, f"{leg} Gamma": 0.001,
                f"{leg} Theta": -0.2, f"{leg} Vega": 0.1,
            })
    chain_df = pd.DataFrame(rows)
    broker = make_broker(
        get_option_chain=lambda **kw: (24450, chain_df),
        get_expiry_list=lambda **kw: ["2026-08-06", "2026-08-13"],
    )
    nifty = Index("NIFTY")
    chain = broker.get_option_chain(nifty, expiry=0, num_strikes=2)
    assert [e.date for e in chain.expiries()] == [date(2026, 8, 6)]
    assert chain.nearest_expiry == date(2026, 8, 6)
