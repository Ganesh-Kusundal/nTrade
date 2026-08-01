"""Live kernel execution tests (Slice G1): DhanBroker wired into TradingKernel.

BrokerExecution → DhanBroker → stubbed Tradehull: no live network. Verifies the
async order lifecycle (accepted → poll → fill/reject), zero-parity with the
simulated path, and broker position/balance reconciliation.
"""

import types
from datetime import datetime, timedelta

import pandas as pd
import pytest

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.portfolio import Position
from ntrade.events.market import TickEvent
from ntrade.events.order import (
    OrderAcceptedEvent, OrderFilledEvent, OrderIntentEvent, OrderRejectedEvent,
)
from ntrade.events.portfolio import BalanceChangedEvent, PositionUpdatedEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


class BuyOnFirstTick(Strategy):
    name = "buy_first"

    def __init__(self, quantity: int = 5):
        super().__init__()
        self.quantity = quantity
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=100.0)
            self.done = True


def _ts(minute: int = 0):
    return datetime(2026, 1, 1, 9, 15) + timedelta(minutes=minute)


def _tick(minute: int = 0):
    return TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=_ts(minute))


def make_broker(**tsl_methods) -> DhanBroker:
    """A real DhanBroker wired to a stubbed Tradehull (like test_dhan_broker)."""
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(**tsl_methods)
    return broker


def _completed_order_status():
    """A stub whose orders go PENDING on placement, then COMPLETE on poll."""
    state = {"filled": False}

    def order_placement(**kw):
        return "ORD-100"

    def get_order_status(orderid=None, **kw):
        return "COMPLETE"

    def get_order_detail(orderid=None, **kw):
        return {"orderId": orderid or "ORD-100", "orderStatus": "COMPLETE",
                "filledQty": 5, "avgPrice": 100.0}

    return order_placement, get_order_status, get_order_detail, state


def test_broker_execution_places_and_accepts():
    op, gos, god, _ = _completed_order_status()
    broker = make_broker(order_placement=op, get_order_status=gos, get_order_detail=god)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    rel = Equity("NIFTY", broker=broker)
    k.register(rel)
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())

    accepted = [e for e in k.bus.history if isinstance(e, OrderAcceptedEvent)]
    assert len(accepted) == 1
    assert accepted[0].order_id == "ORD-100"
    assert accepted[0].symbol == "NIFTY"
    assert k.broker_execution() is not None
    assert k.broker_execution().open_orders() == ["ORD-100"]  # pending, not yet filled


def test_broker_execution_poll_publishes_fill():
    op, gos, god, _ = _completed_order_status()
    broker = make_broker(order_placement=op, get_order_status=gos, get_order_detail=god)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())
    assert len([e for e in k.bus.history if isinstance(e, OrderFilledEvent)]) == 0

    emitted = k.poll_orders()
    assert len(emitted) == 1
    assert isinstance(emitted[0], OrderFilledEvent)
    fill = emitted[0]
    assert fill.order_id == "ORD-100"
    assert fill.quantity == 5 and fill.fill_price == 100.0
    # broker lifecycle advanced → order no longer open
    assert k.broker_execution().open_orders() == []
    # portfolio updated from the broker-reported fill
    pos = k.ctx.portfolio.position("NIFTY")
    assert pos is not None and pos.quantity == 5 and pos.avg_price == 100.0


def test_broker_execution_poll_is_idempotent():
    op, gos, god, _ = _completed_order_status()
    broker = make_broker(order_placement=op, get_order_status=gos, get_order_detail=god)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())
    k.poll_orders()
    assert k.poll_orders() == []  # nothing left open → nothing re-emitted
    assert len([e for e in k.bus.history if isinstance(e, OrderFilledEvent)]) == 1


def test_broker_execution_rejection_on_poll():
    def order_placement(**kw):
        return "ORD-200"

    def get_order_status(orderid=None, **kw):
        return "REJECTED"

    def get_order_detail(orderid=None, **kw):
        return {"orderId": orderid, "orderStatus": "REJECTED", "filledQty": 0, "avgPrice": 0}

    broker = make_broker(order_placement=order_placement,
                         get_order_status=get_order_status,
                         get_order_detail=get_order_detail)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())

    emitted = k.poll_orders()
    assert len(emitted) == 1
    assert isinstance(emitted[0], OrderRejectedEvent)
    assert "REJECTED" in emitted[0].reason
    assert k.ctx.portfolio.position("NIFTY") is None  # no fill, no position
    assert k.broker_execution().open_orders() == []


def test_broker_execution_rejection_on_placement():
    broker = make_broker(
        order_placement=lambda **kw: (_ for _ in ()).throw(RuntimeError("insufficient margin")),
    )
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())

    rejected = [e for e in k.bus.history if isinstance(e, OrderRejectedEvent)]
    assert len(rejected) == 1
    assert "insufficient margin" in rejected[0].reason
    assert not [e for e in k.bus.history if isinstance(e, OrderAcceptedEvent)]
    assert k.broker_execution().open_orders() == []


def test_live_kernel_zero_parity_with_simulated():
    """Same signal → same fills/positions/balance through broker or simulator."""
    # --- simulated reference ---
    sim = TradingKernel(mode="replay", clock=ReplayClock(), initial_cash=100_000.0)
    sim.register(Equity("NIFTY"))
    sim.register_strategy(BuyOnFirstTick())
    sim.run_replay([_tick()])

    # --- live broker kernel ---
    op, gos, god, _ = _completed_order_status()
    broker = make_broker(order_placement=op, get_order_status=gos, get_order_detail=god)
    live = TradingKernel(mode="live", clock=ReplayClock(), broker=broker,
                         initial_cash=100_000.0)
    live.register(Equity("NIFTY", broker=broker))
    live.register_strategy(BuyOnFirstTick())
    live.bus.publish(_tick())
    live.poll_orders()

    sim_pos = sim.ctx.portfolio.position("NIFTY")
    live_pos = live.ctx.portfolio.position("NIFTY")
    assert live_pos is not None and sim_pos is not None
    assert live_pos.quantity == sim_pos.quantity == 5
    assert live_pos.avg_price == sim_pos.avg_price == 100.0
    assert live.ctx.account.balance == sim.ctx.account.balance == pytest.approx(100_000.0 - 500.0)
    # both flows produced exactly one fill with matching content
    sim_fills = [e for e in sim.bus.history if isinstance(e, OrderFilledEvent)]
    live_fills = [e for e in live.bus.history if isinstance(e, OrderFilledEvent)]
    assert len(sim_fills) == len(live_fills) == 1
    assert live_fills[0].fill_price == sim_fills[0].fill_price == 100.0
    assert live_fills[0].quantity == sim_fills[0].quantity == 5


def test_live_kernel_sync_positions_reconciles():
    df = pd.DataFrame([
        {"tradingSymbol": "NIFTY", "netQty": 10, "avgTradingPrice": 101.0, "ltp": 105.0,
         "productType": "MIS", "exchangeSegment": "NSE"},
        {"tradingSymbol": "TCS", "netQty": -3, "avgTradingPrice": 50.0, "ltp": 48.0,
         "productType": "MIS", "exchangeSegment": "NSE"},
    ])
    broker = make_broker(get_positions=lambda: df, get_balance=lambda: 90_000.0)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker,
                      initial_cash=100_000.0)
    k.register(Equity("NIFTY", broker=broker))

    count = k.sync_positions()
    assert count == 2
    nifty = k.ctx.portfolio.position("NIFTY")
    assert nifty is not None and nifty.quantity == 10 and nifty.avg_price == 101.0
    tcs = k.ctx.portfolio.position("TCS")
    assert tcs is not None and tcs.quantity == -3
    assert k.ctx.account.balance == pytest.approx(90_000.0)
    # canonical events were emitted so the rest of the kernel observes the sync
    assert len([e for e in k.bus.history if isinstance(e, PositionUpdatedEvent)]) == 2
    assert len([e for e in k.bus.history if isinstance(e, BalanceChangedEvent)]) == 1


def test_live_kernel_sync_positions_drops_stale():
    df = pd.DataFrame([
        {"tradingSymbol": "NIFTY", "netQty": 5, "avgTradingPrice": 100.0, "ltp": 100.0},
    ])
    broker = make_broker(get_positions=lambda: df, get_balance=lambda: 99_000.0)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker,
                      initial_cash=100_000.0)
    # local position the broker no longer reports → must be removed
    k.ctx.portfolio.positions.append(Position(
        symbol="TCS", quantity=5, avg_price=10.0, ltp=10.0))
    k.sync_positions()
    assert k.ctx.portfolio.position("TCS") is None
    assert k.ctx.portfolio.position("NIFTY") is not None


def test_kernel_sim_mode_has_no_broker_execution():
    k = TradingKernel(mode="replay", clock=ReplayClock())
    assert k.broker_execution() is None
    assert k.poll_orders() == []
    assert k.broker is None
    assert k.position_sync is None


def test_kernel_publish_helper_publishes_intent():
    """Regression guard: TradingKernel.publish() still routes to the bus."""
    k = TradingKernel(mode="replay", clock=ReplayClock())
    intent = OrderIntentEvent(symbol="NIFTY", exchange="NSE", side="BUY",
                              quantity=1, price=100.0, ts=_ts())
    k.publish(intent)
    assert isinstance(k.bus.history[-1], OrderIntentEvent)


# ---------------------------------------------------------------------------
# Regression tests for review findings (order-id fallback, partial fills,
# poll exception safety, position-sync failure handling / change detection).


def test_broker_execution_order_id_fallback():
    """A broker returning no id gets a BRK- id written onto the order."""
    def order_placement(**kw):
        return None  # broker never assigned an id

    def get_order_status(orderid=None, **kw):
        return "COMPLETE"

    def get_order_detail(orderid=None, **kw):
        return {"orderId": orderid, "orderStatus": "COMPLETE",
                "filledQty": 5, "avgPrice": 100.0}

    broker = make_broker(order_placement=order_placement,
                         get_order_status=get_order_status,
                         get_order_detail=get_order_detail)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())

    accepted = [e for e in k.bus.history if isinstance(e, OrderAcceptedEvent)]
    assert len(accepted) == 1 and accepted[0].order_id == "BRK-000001"
    # the tracked order must carry its id so poll() can correlate it
    assert k.broker_execution().open_orders() == ["BRK-000001"]
    emitted = k.poll_orders()
    assert len(emitted) == 1 and emitted[0].quantity == 5
    assert k.broker_execution().open_orders() == []


def test_broker_execution_partial_then_complete_emits_delta():
    """A PARTIAL fill surfaces its shares; a later COMPLETE emits the rest."""
    state = {"status": "PARTIAL"}

    def order_placement(**kw):
        return "ORD-300"

    def get_order_status(orderid=None, **kw):
        return state["status"]

    def get_order_detail(orderid=None, **kw):
        return {"orderId": orderid, "orderStatus": state["status"],
                "filledQty": 5 if state["status"] == "COMPLETE" else 3,
                "avgPrice": 100.0}

    broker = make_broker(order_placement=order_placement,
                         get_order_status=get_order_status,
                         get_order_detail=get_order_detail)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())

    first = k.poll_orders()
    assert len(first) == 1 and first[0].quantity == 3  # partial surfaced
    assert k.broker_execution().open_orders() == ["ORD-300"]  # still open
    state["status"] = "COMPLETE"
    second = k.poll_orders()
    assert len(second) == 1 and second[0].quantity == 2  # delta only
    assert k.broker_execution().open_orders() == []
    fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
    assert [e.quantity for e in fills] == [3, 2]  # 3 + 2 = 5, no double count


def test_broker_execution_partial_then_cancel_keeps_filled_shares():
    """A partial order cancelled/rejected keeps the filled shares as a fill."""
    def order_placement(**kw):
        return "ORD-400"

    def get_order_status(orderid=None, **kw):
        return "REJECTED"

    def get_order_detail(orderid=None, **kw):
        return {"orderId": orderid, "orderStatus": "REJECTED",
                "filledQty": 3, "avgPrice": 100.0}

    broker = make_broker(order_placement=order_placement,
                         get_order_status=get_order_status,
                         get_order_detail=get_order_detail)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())

    emitted = k.poll_orders()
    fills = [e for e in emitted if isinstance(e, OrderFilledEvent)]
    rejects = [e for e in emitted if isinstance(e, OrderRejectedEvent)]
    assert len(fills) == 1 and fills[0].quantity == 3  # filled shares survive
    assert len(rejects) == 1 and rejects[0].quantity == 2  # only the rest rejected
    assert k.broker_execution().open_orders() == []


def test_broker_execution_poll_survives_non_numeric_avg_price():
    """A malformed avg_price from the broker must not abort the poll loop."""
    def order_placement(**kw):
        return "ORD-500"

    def get_order_status(orderid=None, **kw):
        return "COMPLETE"

    def get_order_detail(orderid=None, **kw):
        return {"orderId": orderid, "orderStatus": "COMPLETE",
                "filledQty": 5, "avgPrice": "not-a-number"}

    broker = make_broker(order_placement=order_placement,
                         get_order_status=get_order_status,
                         get_order_detail=get_order_detail)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(_tick())

    emitted = k.poll_orders()  # must not raise
    assert len(emitted) == 1
    assert emitted[0].fill_price == 100.0  # fell back to the intent price
    assert k.broker_execution().open_orders() == []


def test_sync_transient_failure_keeps_state():
    """A failed broker fetch must not wipe positions or zero the balance."""
    broker = make_broker(get_positions=lambda: (_ for _ in ()).throw(RuntimeError("net down")),
                         get_balance=lambda: (_ for _ in ()).throw(RuntimeError("net down")))
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker,
                      initial_cash=100_000.0)
    k.ctx.portfolio.positions.append(Position(
        symbol="NIFTY", quantity=5, avg_price=100.0, ltp=100.0,
        metadata={"strategy": "algo1"}))
    count = k.sync_positions()
    assert count == 1
    assert k.ctx.portfolio.position("NIFTY").quantity == 5
    assert k.ctx.account.balance == pytest.approx(100_000.0)
    assert not [e for e in k.bus.history if isinstance(e, PositionUpdatedEvent)]
    assert not [e for e in k.bus.history if isinstance(e, BalanceChangedEvent)]


def test_sync_is_quiet_when_nothing_changed():
    """sync() emits canonical events only when state actually changed."""
    df = pd.DataFrame([
        {"tradingSymbol": "NIFTY", "netQty": 5, "avgTradingPrice": 100.0, "ltp": 100.0},
    ])
    broker = make_broker(get_positions=lambda: df, get_balance=lambda: 99_000.0)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker,
                      initial_cash=100_000.0)
    k.sync_positions()
    assert len([e for e in k.bus.history if isinstance(e, PositionUpdatedEvent)]) == 1
    assert len([e for e in k.bus.history if isinstance(e, BalanceChangedEvent)]) == 1
    k.sync_positions()  # no change → nothing re-emitted
    assert len([e for e in k.bus.history if isinstance(e, PositionUpdatedEvent)]) == 1
    assert len([e for e in k.bus.history if isinstance(e, BalanceChangedEvent)]) == 1


def test_sync_preserves_local_strategy_metadata():
    """A broker position without metadata keeps the local strategy stamp."""
    df = pd.DataFrame([
        {"tradingSymbol": "NIFTY", "netQty": 5, "avgTradingPrice": 100.0, "ltp": 100.0},
    ])
    broker = make_broker(get_positions=lambda: df, get_balance=lambda: 99_000.0)
    k = TradingKernel(mode="live", clock=ReplayClock(), broker=broker,
                      initial_cash=100_000.0)
    k.ctx.portfolio.positions.append(Position(
        symbol="NIFTY", quantity=5, avg_price=100.0, ltp=100.0,
        metadata={"strategy": "algo1"}))
    k.sync_positions()
    assert k.ctx.portfolio.position("NIFTY").metadata.get("strategy") == "algo1"


def test_sync_adopts_broker_strategy_metadata():
    """A broker position reporting a strategy stamps it on the local position."""
    from ntrade.domain.portfolio import Position as BrokerPosition
    from ntrade.engines.position_sync import PositionSyncEngine

    class StrategyAwareBroker:
        def get_positions(self):
            return [BrokerPosition(symbol="NIFTY", quantity=5, avg_price=100.0,
                                   ltp=100.0, metadata={"strategy": "manual"})]

        def get_balance(self):
            return 99_000.0

    k = TradingKernel(mode="live", clock=ReplayClock(), broker=StrategyAwareBroker(),
                      initial_cash=100_000.0)
    k.ctx.portfolio.positions.append(Position(
        symbol="NIFTY", quantity=5, avg_price=100.0, ltp=100.0,
        metadata={"strategy": "algo1"}))
    k.sync_positions()
    assert k.ctx.portfolio.position("NIFTY").metadata.get("strategy") == "manual"
