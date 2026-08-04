"""Full kernel pipeline tests (Slice C): strategy → risk → OMS → execution → portfolio."""

from datetime import datetime

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.events.risk import SignalRejectedEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


class BuyOnTick(Strategy):
    name = "buy_on_tick"

    def __init__(self, quantity: int = 10, price: float = 100.0, side: str = "BUY"):
        super().__init__()
        self.quantity = quantity
        self.price = price
        self.side = side
        self.emitted = False

    def on_tick(self, event):
        if not self.emitted:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side=self.side, quantity=self.quantity, price=self.price)
            self.emitted = True


class SellOnSecondTick(Strategy):
    name = "sell_second"

    def __init__(self, quantity: int = 10, price: float = 110.0):
        super().__init__()
        self.quantity = quantity
        self.price = price
        self.count = 0

    def on_tick(self, event):
        self.count += 1
        if self.count == 2:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=self.quantity, price=self.price)


def _kernel(**kw):
    kw.setdefault("mode", "replay")
    kw.setdefault("clock", ReplayClock())
    # These tests assert mechanics (fills, netting, risk) with exact balances —
    # opt out of statutory charges so the cost pipeline is not under test here.
    kw.setdefault("statutory", None)
    k = TradingKernel(**kw)
    k.register(Equity("RELIANCE"))
    return k


def _publish_tick(k, price: float, minute: int = 0):
    k.bus.publish(TickEvent(symbol="RELIANCE", exchange="NSE", price=price,
                            ts=datetime(2026, 1, 1, 9, 15, minute * 60)))


def test_pipeline_buy_fills_and_updates_portfolio():
    k = _kernel(initial_cash=100_000.0)
    k.register_strategy(BuyOnTick(quantity=10, price=100.0))
    _publish_tick(k, 101.0)
    fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
    assert len(fills) == 1
    assert fills[0].fill_price == 100.0
    position = k.ctx.portfolio.position("RELIANCE")
    assert position is not None and position.quantity == 10
    assert position.avg_price == 100.0
    assert k.ctx.account.balance == 100_000.0 - 10 * 100.0
    kinds = {type(e).__name__ for e in k.bus.history}
    assert {"SignalGeneratedEvent", "SignalApprovedEvent", "OrderIntentEvent",
            "OrderAcceptedEvent", "OrderFilledEvent", "PositionUpdatedEvent",
            "BalanceChangedEvent"} <= kinds


def test_pipeline_sell_nets_position():
    k = _kernel(initial_cash=100_000.0)
    k.register_strategy(BuyOnTick(quantity=10, price=100.0, side="BUY"))
    k.register_strategy(SellOnSecondTick(quantity=10, price=110.0))
    _publish_tick(k, 100.0)
    _publish_tick(k, 110.0)
    assert k.ctx.portfolio.position("RELIANCE") is None  # netted to zero
    assert k.ctx.account.balance == pytest.approx(100_000.0 - 1000.0 + 1100.0)
    assert len([e for e in k.bus.history if isinstance(e, OrderFilledEvent)]) == 2


def test_risk_rejects_oversized_signal():
    k = _kernel(initial_cash=100_000.0)
    k.risk_engine.max_quantity = 5
    k.register_strategy(BuyOnTick(quantity=100, price=100.0))
    _publish_tick(k, 101.0)
    rejected = [e for e in k.bus.history if isinstance(e, SignalRejectedEvent)]
    assert len(rejected) == 1
    assert "quantity" in rejected[0].reason
    assert k.ctx.portfolio.position("RELIANCE") is None


def test_risk_allowlist_blocks_symbol():
    k = _kernel()
    k.risk_engine.allowlist = {"NIFTY"}
    k.register_strategy(BuyOnTick(quantity=10))
    _publish_tick(k, 101.0)
    rejected = [e for e in k.bus.history if isinstance(e, SignalRejectedEvent)]
    assert len(rejected) == 1
    assert "allowlist" in rejected[0].reason
    assert k.ctx.portfolio.position("RELIANCE") is None


def test_pipeline_requires_initial_quote_for_market_order():
    """A MARKET order with no live price is rejected by the simulated target."""
    from ntrade.engines.strategy_engine import Strategy as S

    class MarketBuy(S):
        name = "market_buy"

        def __init__(self):
            super().__init__()
            self.done = False

        def on_tick(self, event):
            if not self.done:
                self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                                 side="BUY", quantity=10)  # no price → MARKET
                self.done = True

    k = _kernel()
    k.register_strategy(MarketBuy())
    _publish_tick(k, 0.0)  # ltp stays 0
    fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
    assert fills == []


def test_strategy_hook_error_is_logged_and_isolated(caplog):
    """A raising strategy is logged with its traceback and counted, but must
    not take down sibling strategies (H-1: no silent swallow)."""
    import logging
    from ntrade.events.risk import SignalApprovedEvent

    class Broken(Strategy):
        name = "broken"

        def on_tick(self, event):
            raise ValueError("boom")

    k = _kernel()
    broken = Broken()
    k.register_strategy(broken)
    k.register_strategy(BuyOnTick(quantity=10))
    with caplog.at_level(logging.ERROR, logger="ntrade.strategy"):
        _publish_tick(k, 101.0)
    assert "broken" in caplog.text
    assert "boom" in caplog.text  # traceback is part of the record
    assert broken._error_count == 1
    # sibling strategy still ran: its signal was approved and filled
    assert len([e for e in k.bus.history if isinstance(e, SignalApprovedEvent)]) == 1
    assert len([e for e in k.bus.history if isinstance(e, OrderFilledEvent)]) == 1
