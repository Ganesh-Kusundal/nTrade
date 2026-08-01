"""StrategyRunner multi-strategy + per-strategy risk tests (Slice F2)."""

from datetime import datetime

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.events.risk import SignalRejectedEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.kernel.runner import StrategyRunner


class EmitOnce(Strategy):
    """Emits a signal on the first tick it sees."""

    name = "emit_once"

    def __init__(self, quantity: int = 10, price: float = 100.0, side: str = "BUY"):
        super().__init__()
        self.quantity = quantity
        self.price = price
        self.side = side
        self.emitted = False
        self.ticks_seen = 0

    def on_tick(self, event):
        self.ticks_seen += 1
        if not self.emitted:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side=self.side, quantity=self.quantity, price=self.price)
            self.emitted = True


class EmitEveryTick(Strategy):
    """Emits a signal on every tick — dispatch behaviour is observable."""

    name = "every_tick"

    def __init__(self, quantity: int = 1, price: float = 100.0):
        super().__init__()
        self.quantity = quantity
        self.price = price
        self.ticks_seen = 0
        self.signals = 0

    def on_tick(self, event):
        self.ticks_seen += 1
        self.signals += 1
        self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                         side="BUY", quantity=self.quantity, price=self.price)


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), initial_cash=100_000.0)
    k.register(Equity("RELIANCE"))
    return k


def _tick(price: float = 100.0, minute: int = 0):
    return TickEvent(symbol="RELIANCE", exchange="NSE", price=price,
                     ts=datetime(2026, 1, 1, 9, 15 + minute))


def _fills(k):
    return [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]


def test_runner_registers_multiple_strategies():
    k = _kernel()
    runner = StrategyRunner(k)
    a = runner.add(EmitOnce(quantity=5, price=100.0))
    b = runner.add(EmitOnce(quantity=5, price=100.0, side="SELL"))
    assert a == "emit_once"
    assert b == "emit_once#2"  # unique names
    assert sorted(runner.names()) == ["emit_once", "emit_once#2"]

    k.bus.publish(_tick())
    fills = _fills(k)
    # both strategies fire → 2 fills (BUY then SELL), same symbol
    assert len(fills) == 2
    assert sorted(f.order_id for f in fills) == ["SIM-000001", "SIM-000002"]
    assert k.ctx.portfolio.position("RELIANCE") is None  # netted to zero


def test_runner_per_strategy_risk_limits_are_isolated():
    """Strategy A capped at qty 5; strategy B unlimited — both trade RELIANCE."""
    k = _kernel()
    runner = StrategyRunner(k)
    runner.add(EmitOnce(quantity=100, price=100.0), risk={"max_quantity": 5})
    runner.add(EmitOnce(quantity=100, price=100.0), risk={"max_quantity": 1000})

    k.bus.publish(_tick())
    rejected = [e for e in k.bus.history if isinstance(e, SignalRejectedEvent)]
    fills = _fills(k)
    assert len(rejected) == 1
    assert "quantity" in rejected[0].reason
    assert rejected[0].signal.quantity == 100
    # the unrestricted strategy still got its fill
    assert len(fills) == 1
    status = {s["name"]: s for s in runner.status()}
    assert status["emit_once"]["rejected"] == 1
    assert status["emit_once#2"]["approved"] == 1


def test_runner_hot_detach_stops_dispatch():
    k = _kernel()
    runner = StrategyRunner(k)
    strat = EmitEveryTick(quantity=1)
    name = runner.add(strat)
    k.bus.publish(_tick(minute=0))
    k.bus.publish(_tick(minute=1))
    assert strat.ticks_seen == 2
    assert len(_fills(k)) == 2

    # hot detach while the kernel is live
    assert runner.remove(name) is True
    assert name not in runner.names()
    k.bus.publish(_tick(minute=2))
    k.bus.publish(_tick(minute=3))
    assert strat.ticks_seen == 2  # strategy no longer receives events
    assert len(_fills(k)) == 2  # no new fills
    assert runner.remove("nope") is False


def test_runner_enable_disable_toggle():
    k = _kernel()
    runner = StrategyRunner(k)
    strat = EmitEveryTick(quantity=1)
    name = runner.add(strat)
    k.bus.publish(_tick(minute=0))
    k.bus.publish(_tick(minute=1))
    assert strat.ticks_seen == 2

    assert runner.disable(name) is True
    assert runner.running(name) is False
    k.bus.publish(_tick(minute=2))
    k.bus.publish(_tick(minute=3))
    assert strat.ticks_seen == 2  # dispatch skipped while disabled

    assert runner.enable(name) is True
    assert runner.running(name) is True
    k.bus.publish(_tick(minute=4))
    assert strat.ticks_seen == 3
    assert runner.disable("missing") is False


def test_runner_global_risk_paused_while_managing():
    """While the runner owns strategies, the kernel's global RiskEngine is
    paused (a signal must not be screened twice)."""
    k = _kernel()
    runner = StrategyRunner(k)
    runner.add(EmitOnce(quantity=10), risk={"max_quantity": 5})
    k.bus.publish(_tick())
    # the per-strategy engine rejected it; the global one did not double-approve
    rejected = [e for e in k.bus.history if isinstance(e, SignalRejectedEvent)]
    assert len(rejected) == 1
    assert len(_fills(k)) == 0


def test_runner_release_restores_global_risk():
    k = _kernel()
    k.risk_engine.max_quantity = 5
    runner = StrategyRunner(k)
    runner.add(EmitOnce(quantity=10), risk={"max_quantity": 5})
    runner.release()
    assert runner.names() == []
    # after release, a directly-registered strategy hits the kernel's risk engine
    k.register_strategy(EmitOnce(quantity=10))
    k.bus.publish(_tick())
    assert len([e for e in k.bus.history if isinstance(e, SignalRejectedEvent)]) == 1


def test_runner_status_report():
    k = _kernel()
    runner = StrategyRunner(k)
    runner.add(EmitOnce(quantity=10), risk={"max_quantity": 100, "allowlist": {"RELIANCE"}})
    rows = runner.status()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "emit_once"
    assert row["enabled"] is True
    assert row["max_quantity"] == 100
    assert row["allowlist"] == ["RELIANCE"]
    assert row["approved"] == 0 and row["rejected"] == 0


def test_runner_strategy_accessors():
    k = _kernel()
    runner = StrategyRunner(k)
    strat = EmitOnce(quantity=3)
    name = runner.add(strat)
    assert runner.strategy(name) is strat
    assert runner.risk(name) is not None
    assert runner.risk("missing") is None


def test_runner_unique_name_avoids_engine_collision():
    """A strategy directly registered in the kernel reserves its name too."""
    k = _kernel()
    k.register_strategy(EmitOnce(quantity=1))  # directly registered as "emit_once"
    runner = StrategyRunner(k)
    name = runner.add(EmitOnce(quantity=1))
    assert name == "emit_once#2"  # not the colliding base name


def test_runner_release_single_runner_restores_once():
    """Two runners sharing a kernel must not double-restore the global engine."""
    k = _kernel()
    k.risk_engine.max_quantity = 5
    r1 = StrategyRunner(k)
    r2 = StrategyRunner(k)
    r1.add(EmitOnce(quantity=10), risk={"max_quantity": 5})
    r2.add(EmitOnce(quantity=10), risk={"max_quantity": 5})
    # only the first runner actually paused the global engine
    assert getattr(k, "_risk_pause_count", 0) == 2
    r1.release()
    r2.release()
    assert getattr(k, "_risk_pause_count", 0) == 0
    # exactly one handler remains: the global RiskEngine
    from ntrade.events.risk import SignalGeneratedEvent

    handlers = k.bus._subscribers.get(SignalGeneratedEvent, [])
    assert len(handlers) == 1
    assert handlers[0].__self__ is k.risk_engine


def test_runner_max_positions_is_per_strategy():
    """Strategy A's open position must not count against strategy B's cap."""
    k = _kernel()
    runner = StrategyRunner(k)
    runner.add(EmitOnce(quantity=5), risk={"max_positions": 1})
    runner.add(EmitOnce(quantity=5), risk={"max_positions": 1})
    k.bus.publish(_tick())
    rejected = [e for e in k.bus.history if isinstance(e, SignalRejectedEvent)]
    fills = _fills(k)
    # both strategies open one position each; neither trips the other's cap
    assert len(rejected) == 0
    assert len(fills) == 2
