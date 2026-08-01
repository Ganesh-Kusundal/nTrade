"""Regression tests for backlog findings batch: M2, M6, M8, L2, L4, L5."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pandas as pd
import pytest

from ntrade.brokers.dhan import DhanBroker
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.domain.analytics.greeks import BlackScholes, Greeks, NOT_COMPUTED
from ntrade.domain.portfolio import Holding, Position
from ntrade.execution.broker_executor import BrokerExecution
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus
from ntrade.registry import BrokerRegistry


def _ctx(*symbols: str, broker=None) -> TradingContext:
    """A live context with the given symbols registered as broker-backed."""
    from ntrade.domain.instruments.cash import Equity
    ctx = TradingContext(EventBus(), LiveClock(), mode="live", instruments={}, session_id="")
    for sym in symbols:
        ctx.instruments[sym] = Equity(sym, broker=broker)
    return ctx


def _no_id_broker() -> DhanBroker:
    """A stub DhanBroker that never assigns its own order id."""
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types = __import__("types").SimpleNamespace(
        order_placement=lambda **kw: None,
        get_order_status=lambda orderid=None, **kw: "COMPLETE",
        get_order_detail=lambda orderid=None, **kw: {
            "orderId": orderid, "orderStatus": "COMPLETE",
            "filledQty": 1, "avgPrice": 100.0},
    )
    return broker


def _intent(symbol="NIFTY", quantity=1, ts=None):
    from ntrade.events.order import OrderIntentEvent
    return OrderIntentEvent(
        symbol=symbol, exchange="NSE", side="BUY", quantity=quantity,
        order_type="LIMIT", price=100.0, strategy="", ts=ts or datetime.now(),
    )


# ---------------------------------------------------------------------------
# M2 — BRK- fallback order id merging


def test_m2_fallback_ids_never_merge_with_open_orders():
    """A broker returning no id must get unique BRK- ids, never colliding
    with an already-open order (two orders must not merge under one key)."""
    broker = _no_id_broker()
    ctx = _ctx("NIFTY", "TCS", broker=broker)
    exe = BrokerExecution(ctx, broker)

    # First order with no broker id → BRK-000001
    exe.submit(_intent())
    assert exe.open_orders() == ["BRK-000001"]

    # Second order with no broker id → BRK-000002 (never merges into 000001)
    exe.submit(_intent(symbol="TCS"))
    assert exe.open_orders() == ["BRK-000001", "BRK-000002"]
    assert len(set(exe.open_orders())) == 2  # no key merging


def test_m2_fallback_does_not_clobber_broker_id():
    """A broker-assigned id must be kept as-is; the fallback only fires when
    the broker truly returns nothing."""
    # Simulate a broker that DOES assign an id on the second order.
    class _Sequenced:
        def __init__(self):
            self.calls = 0
        def order_placement(self, **kw):
            self.calls += 1
            return None if self.calls == 1 else "REAL-900"

    b2 = DhanBroker.__new__(DhanBroker)
    b2._connected = True
    seq = _Sequenced()
    b2.tsl = __import__("types").SimpleNamespace(
        order_placement=seq.order_placement,
        get_order_status=lambda orderid=None, **kw: "COMPLETE",
        get_order_detail=lambda orderid=None, **kw: {
            "orderId": orderid, "orderStatus": "COMPLETE",
            "filledQty": 1, "avgPrice": 100.0},
    )
    ctx = _ctx("NIFTY", "TCS", broker=b2)
    exe2 = BrokerExecution(ctx, b2)
    exe2.submit(_intent())
    exe2.submit(_intent(symbol="TCS"))
    assert exe2.open_orders() == ["BRK-000001", "REAL-900"]


# ---------------------------------------------------------------------------
# M6 — Scanner rate-limiting + snapshot semantics


def test_m6_rate_limited_scanner_serves_cached_results():
    from ntrade.domain.scanner import Scanner, ScannerFacade, ScannerResult
    from ntrade.kernel.trading_session import TradingSession

    calls = {"n": 0}

    class CountingScanner(Scanner):
        name = "counting"
        rate_limit_seconds = 60.0

        def scan(self, session, **kw):
            calls["n"] += 1
            return [ScannerResult(instrument=object(), scanner_name=self.name,
                                  score=1.0, signal="BUY")]

    session = TradingSession.paper()
    facade = ScannerFacade(session)
    scanner = CountingScanner()  # one instance — cache keyed by id(scanner)
    t0 = datetime(2026, 1, 1, 9, 15)
    first = facade._run(scanner, now=t0)
    assert calls["n"] == 1 and len(first) == 1
    # second call within the window → cached, scan NOT re-run
    second = facade._run(scanner, now=t0 + timedelta(seconds=5))
    assert calls["n"] == 1 and len(second) == 1
    # after the window → re-scan
    facade._run(scanner, now=t0 + timedelta(seconds=61))
    assert calls["n"] == 2


def test_m6_unlimited_scanner_runs_every_time():
    from ntrade.domain.scanner import Scanner, ScannerFacade, ScannerResult
    from ntrade.kernel.trading_session import TradingSession

    calls = {"n": 0}

    class FastScanner(Scanner):
        name = "fast"

        def scan(self, session, **kw):
            calls["n"] += 1
            return []

    session = TradingSession.paper()
    facade = ScannerFacade(session)
    scanner = FastScanner()
    t0 = datetime(2026, 1, 1, 9, 15)
    facade._run(scanner, now=t0)
    facade._run(scanner, now=t0 + timedelta(milliseconds=1))
    assert calls["n"] == 2  # no rate limit → always re-scan


# ---------------------------------------------------------------------------
# M8 — pandas leak at transport boundary


def test_m8_transport_positions_are_domain_objects():
    df = pd.DataFrame([
        {"tradingSymbol": "NIFTY", "netQty": 10, "avgTradingPrice": 101.0,
         "ltp": 105.0, "productType": "MIS", "exchangeSegment": "NSE"},
        {"tradingSymbol": "TCS", "netQty": -3, "avgTradingPrice": 50.0,
         "ltp": 48.0, "productType": "MIS", "exchangeSegment": "NSE"},
    ])
    transport = DhanTransport(
        __import__("types").SimpleNamespace(get_positions=lambda: df,
                                            get_holdings=lambda: df),
    )
    positions = transport.get_positions()
    assert all(isinstance(p, Position) for p in positions)
    assert positions[0].symbol == "NIFTY" and positions[0].quantity == 10
    holdings = transport.get_holdings()
    assert all(isinstance(h, Holding) for h in holdings)
    # no pandas DataFrame ever leaks past the transport boundary
    assert not any(isinstance(x, pd.DataFrame) for x in (positions, holdings))


# ---------------------------------------------------------------------------
# L2 — Market facade consolidation


def test_l2_market_delegates_to_trading_session():
    from ntrade.facade import Market
    from ntrade.kernel.trading_session import TradingSession

    m = Market(broker="paper")
    assert isinstance(m._session, TradingSession)  # single implementation
    assert m.broker is m._session.broker
    rel = m.equity("RELIANCE")
    assert rel.broker_adapter is m.broker
    assert m.balance() == 100_000.0
    assert m.positions() == []
    assert m.live_pnl() == 0.0
    # Market's instruments factory is the session's (single implementation)
    assert m.instruments is m._session.factory


def test_l2_market_chain_via_session():
    from ntrade.facade import Market

    m = Market(broker="paper")
    nifty = m.index("NIFTY")
    nifty._quote = nifty._quote.with_update(ltp=24550.0)
    chain = m.chain(nifty, num_strikes=7)
    assert len(chain) == 14


# ---------------------------------------------------------------------------
# L4 — BrokerRegistry class-level lock


def test_l4_registry_survives_concurrent_access():
    BrokerRegistry.unregister_all()
    errors = []

    def writer():
        try:
            for i in range(50):
                BrokerRegistry.register(f"w{i}", lambda **kw: object())
                BrokerRegistry.unregister_all()
                BrokerRegistry.register("paper", lambda **kw: object())
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def reader():
        try:
            for _ in range(50):
                BrokerRegistry.available()
                _ = "paper" in BrokerRegistry.available()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(3)] + \
              [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors  # no race exceptions


# ---------------------------------------------------------------------------
# L5 — Greeks not-computed sentinel


def test_l5_iv_defaults_to_not_computed():
    g = Greeks()
    assert g.iv is NOT_COMPUTED
    assert g.computed is False  # zero-filled default is NOT a real computation
    assert Greeks(delta=0.55).computed is True


def test_l5_implied_volatility_returns_none_on_failure():
    # Deep ITM price below intrinsic → no solution → NOT_COMPUTED, not 0.0
    iv = BlackScholes.implied_volatility(market_price=1.0, spot=100, strike=50,
                                         years=0.5, risk_free=0.065, option_type="CE")
    assert iv is NOT_COMPUTED
    # years <= 0 → NOT_COMPUTED
    assert BlackScholes.implied_volatility(5.0, 100, 100, 0.0, 0.065) is NOT_COMPUTED
    # valid case still returns a float
    assert isinstance(
        BlackScholes.implied_volatility(8.0, 100, 100, 0.5, 0.065), float)


def test_l5_option_iv_defaults_none_and_keeps_real_iv():
    from datetime import date
    from ntrade.domain.instruments.derivatives import Option

    opt = Option("NIFTY 24500 CE", strike=24500, expiry=date(2026, 9, 1),
                 option_type="CE", underlying_symbol="NIFTY")
    assert opt.iv is None  # not computed, never a fake 0.0
    opt.set_greeks(Greeks(delta=0.55, iv=0.18))
    assert opt.iv == 0.18
