"""T-027 integration checks for the broker rate gate (BrokerRateGate).

Proves end-to-end behavior the unit tests only imply: quote-class spacing at
1/s, shared gate across threads, order path paying ORDER quota with DH-904
propagating as RateLimited, class-scoped backoff after DH-904, and PaperBroker
being unaffected. All fake-clock; never hits Dhan.
"""

from __future__ import annotations

import threading
import time
from datetime import date

import pandas as pd
import pytest
from unittest.mock import MagicMock

from ntrade.brokers.dhan import DhanBroker
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity, Index
from ntrade.domain.orders.order import Order, OrderSide, OrderType, TradeType
from ntrade.execution.rate_limit import (
    DEFAULT_WINDOWS, BrokerRateGate, Quota, RateLimited,
)


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_gated_broker(tsl, *, windows: dict | None = None):
    """A real DhanBroker wired to a stubbed Tradehull + fake-clock gate."""
    clock = FakeClock()
    gate = BrokerRateGate(clock=clock, sleep=clock.sleep, windows=windows)
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = tsl
    broker._gate = gate
    broker._transport = DhanTransport(tsl, gate=gate, clock=None)
    return broker, clock, gate


class TestQuoteClassSpacing:
    def test_quote_class_spaced_at_1_per_second(self):
        """Each get_quote consumes 2 QUOTE tokens (ltp + quote_data); two
        back-to-back calls block the second until >= 2s total spacing."""
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"RELIANCE": 2500.0}
        tsl.get_quote_data.return_value = {"RELIANCE": {"high": 2510.0, "low": 2490.0}}
        broker, clock, gate = make_gated_broker(tsl)
        rel = Equity("RELIANCE")
        rel._broker = broker
        rel._quote = rel._quote.with_update(ltp=2500.0)

        broker.get_quote(rel)
        broker.get_quote(rel)  # must block on the QUOTE window
        # 2 calls x 2 tokens = 4 tokens at 1/s -> second call waits >= 2s
        assert clock.t >= 2.0
        assert clock.t >= 4 * 1.0 - 2.0  # loose lower bound for the 2nd call
        assert tsl.get_ltp_data.call_count == 2
        assert tsl.get_quote_data.call_count == 2

    def test_quote_and_data_classes_are_independent(self):
        """A saturated QUOTE class must not block a DATA class call."""
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"RELIANCE": 2500.0}
        tsl.get_quote_data.return_value = {"RELIANCE": {}}
        tsl.get_historical_data.return_value = pd.DataFrame({
            "Timestamp": ["2026-08-01 09:15:00"],
            "Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10],
        })
        broker, clock, gate = make_gated_broker(tsl)
        rel = Equity("RELIANCE")
        rel._broker = broker
        rel._quote = rel._quote.with_update(ltp=2500.0)
        idx = Index("NIFTY")
        idx._broker = broker

        broker.get_quote(rel)         # QUOTE window now has 2 tokens
        clock.t = 0.1
        broker.get_historical(idx, timeframe="5m")  # DATA class, no QUOTE wait
        assert clock.t == 0.1         # no artificial sleep on the DATA path
        assert tsl.get_historical_data.call_count == 1


class TestSharedGateAcrossThreads:
    def test_shared_gate_across_instruments_and_threads(self):
        """One gate shared by a transport; concurrent LTP+history calls never
        exceed their class windows (loose wall-clock bound)."""
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"RELIANCE": 2500.0}
        tsl.get_historical_data.return_value = pd.DataFrame({
            "Timestamp": ["2026-08-01 09:15:00"],
            "Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10],
        })
        windows = dict(DEFAULT_WINDOWS)
        windows[Quota.QUOTE] = ((0.02, 1),)     # fast 20ms windows
        windows[Quota.DATA] = ((0.02, 5),)
        broker, clock, gate = make_gated_broker(tsl, windows=windows)

        errors: list[Exception] = []

        def ltp_loop():
            try:
                for _ in range(3):
                    broker._transport.get_ltp("RELIANCE")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def history_loop():
            try:
                for _ in range(3):
                    broker._transport.get_historical("NIFTY", "NSE", "5m")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=ltp_loop)
        t2 = threading.Thread(target=history_loop)
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)
        assert not errors
        assert not t1.is_alive() and not t2.is_alive()
        # LTP is 1/20ms -> 3 calls serialize to >= 40ms elapsed wall clock
        assert clock.t >= 0.02 * 2


class TestOrderPath:
    def test_order_path_pays_order_quota_and_propagates_rejection(self):
        """place_order acquires the ORDER window; a DH-904 surfaces as
        RateLimited (never a silent reject->PENDING)."""
        tsl = MagicMock()
        tsl.order_placement.side_effect = RuntimeError("DH-904 rate limit")
        broker, clock, gate = make_gated_broker(tsl)
        rel = Equity("RELIANCE")
        rel._broker = broker
        order = Order(instrument=rel, side=OrderSide.BUY, quantity=10,
                      order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=2500.0)
        with pytest.raises(RateLimited):
            broker.place_order(order)
        assert len(gate._history[Quota.ORDER][0]) == 1  # ORDER token consumed
        assert len(gate._history[Quota.QUOTE][0]) == 0  # no QUOTE tokens

    def test_penalize_on_dh904_backs_off_class(self):
        """A DH-904 on DATA backs off DATA only; QUOTE still proceeds; the next
        DATA call is blocked for retry_after."""
        tsl = MagicMock()
        tsl.get_historical_data.side_effect = RateLimited(Quota.DATA, retry_after=3.0)
        broker, clock, gate = make_gated_broker(tsl)
        idx = Index("NIFTY")
        idx._broker = broker

        with pytest.raises(RateLimited):
            broker.get_historical(idx, timeframe="5m")
        assert gate._cooldown_until[Quota.DATA] >= clock.t + 3.0

        # QUOTE still proceeds during the DATA cooldown (class-scoped backoff)
        tsl.get_ltp_data.return_value = {"RELIANCE": 2500.0}
        tsl.get_quote_data.return_value = {"RELIANCE": {}}
        rel = Equity("RELIANCE")
        rel._broker = broker
        rel._quote = rel._quote.with_update(ltp=2500.0)
        broker.get_quote(rel)
        assert tsl.get_ltp_data.call_count == 1

        # A subsequent DATA call must wait out the remaining penalty
        tsl.get_historical_data.side_effect = None
        tsl.get_historical_data.return_value = pd.DataFrame({
            "Timestamp": ["2026-08-01 09:15:00"],
            "Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10],
        })
        before = clock.t
        broker.get_historical(idx, timeframe="5m")
        # The DATA acquire must have waited out the cooldown (until t >= 3.0)
        assert clock.t >= 3.0


class TestPositionSyncPath:
    def test_position_sync_pays_nontrading_quota(self):
        """T-037: PositionSyncEngine.sync() calls broker.get_positions() /
        get_balance(), which route through the transport's NON_TRADING gate —
        the timer-driven reconciliation path is not a rate-limit bypass."""
        from ntrade.engines.position_sync import PositionSyncEngine

        tsl = MagicMock()
        tsl.get_positions.return_value = []
        tsl.get_balance.return_value = 100_000.0
        broker, _, gate = make_gated_broker(tsl)

        ctx = MagicMock()
        ctx.portfolio.positions = []
        ctx.portfolio.position = lambda symbol: None
        ctx.account.balance = 99_000.0
        ctx.bus.publish = MagicMock()
        ctx.now = lambda: date(2026, 8, 2)

        engine = PositionSyncEngine(ctx, broker)
        engine.sync()

        # positions + balance = 2 NON_TRADING tokens consumed via the gate
        assert len(gate._history[Quota.NON_TRADING][0]) == 2
        assert tsl.get_positions.call_count == 1
        assert tsl.get_balance.call_count == 1


class TestPaperBrokerUnaffected:
    def test_paper_broker_unaffected(self):
        """PaperBroker has no gate and no new failure modes."""
        broker = PaperBroker()
        assert not hasattr(broker, "_gate")
        broker.seed_quote("RELIANCE", ltp=100.0)
        rel = Equity("RELIANCE", broker=broker)
        order = rel.order.buy(quantity=10, price=100.0)
        assert order.order_id
        assert order.status.value == "COMPLETED"
        # History/quote reads still work without any rate infrastructure
        assert broker.get_quote(rel).ltp == 100.0
