"""reference_price fill-price tests — zero-parity fix (Tasks A-D).

The strategy fires on CandleClosedEvent and currently fills MARKET orders from
``instrument._quote.ltp``, which by then may be contaminated by the next bar's
data (look-ahead bias). The fix carries the bar-close price as
``reference_price`` through the signal → intent → execution chain so
backtest, replay and paper all fill at the bar close that generated the
signal.

Covered here:
  * ``OrderIntentEvent.reference_price`` / ``Order.reference_price`` fields
    exist with default 0.0 (0.0 = "use live LTP").
  * ``OrderFacade.place`` passes ``reference_price`` through to ``Order``.
  * ``Strategy.emit_signal(reference_price=...)`` lands it in metadata.
  * ``OrderEngine`` carries it from the signal into the ``OrderIntentEvent``.
  * ``SimulatedExecution`` fills MARKET at ``reference_price`` (not the
    contaminated ltp) and falls back to ltp / rejects when unavailable.
  * ``BrokerExecution`` passes it through to ``instrument.order.place``.
  * ``PaperBroker`` fills at ``order.reference_price`` when set (> 0).
  * End-to-end: a bar-driven MARKET entry fills at the bar close, never at
    a contaminated ltp (the look-ahead bias regression).
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.orders.order import Order, OrderSide, OrderType
from ntrade.events.market import CandleClosedEvent
from ntrade.events.order import (
    OrderFilledEvent, OrderIntentEvent, OrderRejectedEvent,
)
from ntrade.events.risk import SignalGeneratedEvent
from ntrade.execution.broker_executor import BrokerExecution
from ntrade.execution.simulator import SimulatedExecution
from ntrade.kernel.clock import LiveClock, ReplayClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus
from ntrade.kernel.session import TradingKernel

_TS = datetime(2026, 8, 3, 10, 0)
_NIFTY = "NIFTY"


def _kernel():
    """Replay kernel over one registered NIFTY equity (sim execution target)."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=1_000_000.0, statutory=None)
    k.register(Equity(_NIFTY))
    return k


def _candle(k, *, close, ts=_TS, open_=None, high=None, low=None, volume=100):
    """Publish one CandleClosedEvent (plus nothing else — the caller controls
    the instrument quote state to simulate contamination)."""
    o = open_ if open_ is not None else close - 0.5
    h = high if high is not None else max(close, o) + 0.5
    lo = low if low is not None else min(close, o) - 0.5
    k.bus.publish(CandleClosedEvent(
        symbol=_NIFTY, exchange="NSE", timeframe="1m",
        open=o, high=h, low=lo, close=close, volume=volume, ts=ts,
    ))


# ------------------------------------------------------------------ fields

def test_order_intent_event_carries_reference_price_default_zero():
    intent = OrderIntentEvent(symbol=_NIFTY, exchange="NSE", side="BUY",
                              quantity=10, ts=_TS)
    assert intent.reference_price == 0.0


def test_order_carries_reference_price_default_zero():
    order = Order(instrument=Equity(_NIFTY), side=OrderSide.BUY, quantity=10)
    assert order.reference_price == 0.0


def test_order_facade_place_passes_reference_price_through():
    """OrderFacade.place(**kwargs) flows reference_price into Order()."""
    broker = PaperBroker()
    eq = Equity(_NIFTY)
    eq._broker = broker
    order = eq.order.place(OrderSide.BUY, 10, order_type=OrderType.MARKET,
                           reference_price=123.0)
    assert order.reference_price == 123.0


# --------------------------------------------------------- signal → intent

class _RefSignalStrategy:
    """Minimal strategy that emits a MARKET signal with reference_price."""

    name = "ref_signal"

    def __init__(self, reference):
        self.reference = reference

    def on_candle_closed(self, event):
        from ntrade.engines.strategy_engine import Strategy
        self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                         side="BUY", quantity=10, price=0.0,  # MARKET
                         reference_price=float(self.reference))


def test_emit_signal_reference_price_lands_in_metadata():
    from ntrade.engines.strategy_engine import Strategy

    k = _kernel()
    strat = Strategy.__new__(Strategy)
    strat.name = "ref"
    strat.enabled = True
    k.register_strategy(strat)
    strat.emit_signal(symbol=_NIFTY, exchange="NSE", side="BUY", quantity=10,
                      price=0.0, reference_price=100.5)
    signals = [e for e in k.bus.history if isinstance(e, SignalGeneratedEvent)]
    assert len(signals) == 1
    assert signals[0].metadata.get("reference_price") == 100.5


def test_emit_signal_without_reference_price_leaves_metadata_clean():
    from ntrade.engines.strategy_engine import Strategy

    k = _kernel()
    strat = Strategy.__new__(Strategy)
    strat.name = "noref"
    strat.enabled = True
    k.register_strategy(strat)
    strat.emit_signal(symbol=_NIFTY, exchange="NSE", side="BUY", quantity=10,
                      price=0.0)
    signals = [e for e in k.bus.history if isinstance(e, SignalGeneratedEvent)]
    assert len(signals) == 1
    assert "reference_price" not in signals[0].metadata


def test_signal_to_intent_carries_reference_price():
    from ntrade.engines.strategy_engine import Strategy

    class RefSignal(Strategy):
        name = "ref_signal"

        def on_candle_closed(self, event):
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=10, price=0.0,
                             reference_price=float(event.close))

    k = _kernel()
    k.register_strategy(RefSignal())
    _candle(k, close=100.5)
    intents = [e for e in k.bus.history if isinstance(e, OrderIntentEvent)]
    assert len(intents) == 1
    assert intents[0].order_type == "MARKET"
    assert intents[0].reference_price == 100.5


# ------------------------------------------------- simulated execution

def _sim_ctx(ltp: float | None):
    bus = EventBus()
    clock = ReplayClock(start=_TS)
    eq = Equity(_NIFTY)
    if ltp is not None:
        eq._quote = eq._quote.with_update(ltp=ltp)
    ctx = TradingContext(bus, clock, mode="replay",
                         instruments={_NIFTY: eq}, session_id="")
    return ctx


def test_simulated_execution_fills_market_at_reference_price():
    """MARKET intent fills at reference_price even when ltp is contaminated
    (the next bar already moved the quote) — the look-ahead regression."""
    ctx = _sim_ctx(ltp=999.0)  # contaminated ltp
    exe = SimulatedExecution(ctx, statutory=None)
    intent = OrderIntentEvent(symbol=_NIFTY, exchange="NSE", side="BUY",
                              quantity=10, order_type="MARKET",
                              reference_price=100.5, ts=_TS)
    assert exe.submit(intent) is None
    fills = [e for e in ctx.bus.history if isinstance(e, OrderFilledEvent)]
    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(100.5)


def test_simulated_execution_falls_back_to_ltp_without_reference():
    ctx = _sim_ctx(ltp=555.0)
    exe = SimulatedExecution(ctx, statutory=None)
    intent = OrderIntentEvent(symbol=_NIFTY, exchange="NSE", side="BUY",
                              quantity=10, order_type="MARKET", ts=_TS)
    exe.submit(intent)
    fills = [e for e in ctx.bus.history if isinstance(e, OrderFilledEvent)]
    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(555.0)


def test_simulated_execution_rejects_market_without_any_price():
    ctx = _sim_ctx(ltp=None)  # no quote at all
    exe = SimulatedExecution(ctx, statutory=None)
    intent = OrderIntentEvent(symbol=_NIFTY, exchange="NSE", side="BUY",
                              quantity=10, order_type="MARKET", ts=_TS)
    outcome = exe.submit(intent)
    assert isinstance(outcome, OrderRejectedEvent)
    assert "no market price" in outcome.reason


# ------------------------------------------------- broker execution

def test_broker_execution_passes_reference_price_to_place():
    broker = MagicMock()

    def place(order):
        order.order_id = "D-1"
        return order

    broker.place_order.side_effect = place
    eq = Equity(_NIFTY)
    eq._broker = broker
    bus = EventBus()
    clock = LiveClock()
    ctx = TradingContext(bus, clock, mode="live",
                         instruments={_NIFTY: eq}, session_id="")
    exe = BrokerExecution(ctx, broker, statutory=None)
    intent = OrderIntentEvent(symbol=_NIFTY, exchange="NSE", side="BUY",
                              quantity=10, order_type="MARKET",
                              reference_price=100.5, ts=datetime.now())
    assert exe.submit(intent) is None
    placed = broker.place_order.call_args[0][0]
    assert placed.reference_price == 100.5


# --------------------------------------------------------- paper broker

def test_paper_broker_fills_market_at_reference_price():
    broker = PaperBroker()
    eq = Equity(_NIFTY)
    eq._broker = broker
    eq._quote = eq._quote.with_update(ltp=999.0)  # contaminated live quote
    order = Order(instrument=eq, side=OrderSide.BUY, quantity=10,
                  order_type=OrderType.MARKET, reference_price=100.5)
    broker.place_order(order)
    assert order.avg_price == pytest.approx(100.5)


def test_paper_broker_uses_live_ltp_when_reference_zero():
    broker = PaperBroker()
    eq = Equity(_NIFTY)
    eq._broker = broker
    eq._quote = eq._quote.with_update(ltp=777.0)
    order = Order(instrument=eq, side=OrderSide.BUY, quantity=10,
                  order_type=OrderType.MARKET)
    broker.place_order(order)
    assert order.avg_price == pytest.approx(777.0)


def test_paper_broker_falls_back_to_seeded_quote():
    broker = PaperBroker()
    eq = Equity(_NIFTY)
    eq._broker = broker
    order = Order(instrument=eq, side=OrderSide.BUY, quantity=10,
                  order_type=OrderType.MARKET)
    broker.place_order(order)
    assert order.avg_price == pytest.approx(100.0)  # seeded quote default


# ------------------------------------------------ end-to-end regression

def test_bar_driven_market_entry_fills_at_reference_close_not_contaminated_ltp():
    """The core look-ahead regression: the signal fires on CandleClosedEvent,
    but instrument._quote.ltp already reflects the NEXT bar's price. The
    MARKET fill must use the bar-close reference, never the contaminated ltp."""
    from ntrade.engines.strategy_engine import Strategy

    class RefSignal(Strategy):
        name = "ref_signal"

        def on_candle_closed(self, event):
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=10, price=0.0,
                             reference_price=float(event.close))

    k = _kernel()
    k.register_strategy(RefSignal())
    # Contaminate the quote with the NEXT bar's price BEFORE the closed-candle
    # event for the signal bar is processed (exactly what happens in a
    # bar-driven backtest when the candle closes on the next bar's quote).
    k.ctx.instrument(_NIFTY)._quote = k.ctx.instrument(_NIFTY)._quote.with_update(ltp=999.0)
    _candle(k, close=100.5)
    fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(100.5), (
        "MARKET entry must fill at the bar-close reference price (100.5), "
        f"not the contaminated ltp ({fills[0].fill_price})"
    )


def test_valentini_entry_signal_carries_reference_price():
    """ValentiniScalper._emit_entry passes the bar close as reference_price."""
    from ntrade.engines.strategies import ValentiniScalper
    from ntrade.domain.analytics.volume_profile import VolumeProfile, VPLevel

    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5,
                             fade_extended=False)
    k.register_strategy(strat)
    prof = VolumeProfile(levels=tuple(
        VPLevel(price=p, volume=1.0) for p in (110.0, 118.0, 126.0)),
        poc=118.0, vah=126.0, val=110.0, step=4.0)
    import ntrade.engines.strategies as _s
    _orig = _s.build_volume_profile
    _s.build_volume_profile = lambda *a, **kw: prof
    try:
        for i in range(30):
            c = 100.0 + i * 0.5
            _candle(k, close=c, ts=_TS + timedelta(minutes=i))
        _candle(k, close=110.0, open_=110.0, high=110.05, low=109.95,
                volume=3000, ts=_TS + timedelta(minutes=30))
        _candle(k, close=118.0, ts=_TS + timedelta(minutes=31))
        _candle(k, close=122.0, ts=_TS + timedelta(minutes=32))
    finally:
        _s.build_volume_profile = _orig
    signals = [s for s in k.bus.history
               if isinstance(s, SignalGeneratedEvent)
               and s.metadata.get("phase") == "signal"]
    assert signals, "expected a Valentini entry signal"
    assert signals[0].metadata.get("reference_price") == 122.0
