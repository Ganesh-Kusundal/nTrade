"""Tests for the order model and instrument-bound OrderFacade."""

from datetime import date

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity, ETF
from ntrade.domain.instruments.derivatives import Future, Option
from ntrade.domain.orders.order import OrderSide, OrderStatus, OrderType, TradeType


def test_buy_limit_order():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=10, price=2500.0)
    assert order.side == OrderSide.BUY
    assert order.status == OrderStatus.COMPLETED
    assert order.order_id is not None
    assert order.filled_qty == 10


def test_sell_market_order():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.market(OrderSide.SELL, quantity=5)
    assert order.side == OrderSide.SELL
    assert order.avg_price == 2500.0


def test_orders_tracked_by_broker():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    rel.order.buy(quantity=10, price=2500.0)
    rel.order.sell(quantity=5, price=2505.0)
    assert len(broker.orders) == 2


def test_place_without_broker_raises():
    rel = Equity("RELIANCE")
    try:
        rel.order.buy(quantity=10)
        assert False, "should have raised"
    except RuntimeError:
        pass


def test_order_is_open_flags():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=10, price=2500.0)
    assert not order.is_open  # completed on paper broker
    assert order.is_filled


def test_stop_order():
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.stop(OrderSide.SELL, quantity=10, price=2490.0, trigger_price=2495.0)
    assert order.order_type == OrderType.STOP_LIMIT
    assert order.trigger_price == 2495.0


def test_option_limit_price_must_not_be_market():
    """SEBI: F&O market orders banned — DhanBroker force-converts to LIMIT."""
    from datetime import date

    import types

    from ntrade.brokers.dhan import DhanBroker
    opt = Option("NIFTY 24500 CE", exchange="NFO", strike=24500, expiry=date.today(),
                 option_type="CE", underlying_symbol="NIFTY")
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(order_placement=lambda **kw: "ORD-1")
    opt._broker = broker
    opt._quote = opt._quote.with_update(ltp=50.0)
    order = opt.order.market(OrderSide.BUY, quantity=75)
    assert order.order_type == OrderType.LIMIT
    assert order.price > 50.0  # 2% above LTP for instant fill


def test_order_created_at_defaults_to_none():
    from ntrade.domain.orders.order import Order, OrderSide, OrderType
    from unittest.mock import MagicMock
    inst = MagicMock()
    o = Order(instrument=inst, side=OrderSide.BUY, quantity=10)
    assert o.created_at is None


# ------------------------------------------------------------- K-024


def test_equity_order_defaults_cnc():
    """K-024: an Equity without an explicit trade_type must default to CNC
    (delivery), not MIS (intraday margin) — the silent wrong-product-type fix."""
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=75, price=100.0)
    assert order.trade_type == TradeType.CNC


def test_etf_order_defaults_cnc():
    broker = PaperBroker()
    broker.seed_quote("NIFTYBEES", ltp=250.0)
    etf = ETF("NIFTYBEES", broker=broker)
    order = etf.order.buy(quantity=10, price=250.0)
    assert order.trade_type == TradeType.CNC


def test_derivative_order_defaults_mis():
    """Options/futures keep the intraday MIS default."""
    broker = PaperBroker()
    broker.seed_quote("NIFTY", ltp=24500.0)
    opt = Option("NIFTY 24500 CE", exchange="NFO", strike=24500, expiry=date.today(),
                 option_type="CE", underlying_symbol="NIFTY", broker=broker)
    opt._quote = opt._quote.with_update(ltp=50.0)
    order = opt.order.buy(quantity=75, price=50.0)
    assert order.trade_type == TradeType.MIS


def test_future_order_defaults_mis():
    broker = PaperBroker()
    broker.seed_quote("NIFTY", ltp=24500.0)
    fut = Future("NIFTY 26AUG", exchange="NFO", underlying="NIFTY",
                 expiry=date(2026, 8, 27), broker=broker)
    order = fut.order.buy(quantity=75, price=24500.0)
    assert order.trade_type == TradeType.MIS


def test_facade_helpers_use_kind_default():
    """limit/market/stop/cover/bracket must all resolve the kind default too."""
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    assert rel.order.limit("BUY", 10, 2500.0).trade_type == TradeType.CNC
    assert rel.order.market("SELL", 5).trade_type == TradeType.CNC
    assert rel.order.stop("SELL", 10, 2490.0, 2495.0).trade_type == TradeType.CNC
    assert rel.order.cover("SELL", 10).trade_type == TradeType.CNC


def test_explicit_trade_type_wins():
    """A caller-passed trade_type always beats the kind default."""
    broker = PaperBroker()
    broker.seed_quote("RELIANCE", ltp=2500.0)
    rel = Equity("RELIANCE", broker=broker)
    order = rel.order.buy(quantity=10, price=2500.0, trade_type=TradeType.MIS)
    assert order.trade_type == TradeType.MIS  # explicit MIS on equity stays MIS
