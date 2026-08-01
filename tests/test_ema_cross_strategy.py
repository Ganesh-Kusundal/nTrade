"""EmaCrossStrategy tests — golden/death cross signals through the kernel flow."""

from datetime import datetime, timedelta

from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategies import EmaCrossStrategy
from ntrade.events.market import CandleClosedEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel

_TS = datetime(2026, 1, 1, 9, 15)


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="5m",
                      initial_cash=100_000.0)
    k.register(Equity("RELIANCE"))
    return k


def _candle(k, close, ema_fast, ema_slow, i):
    """Publish a closed candle with the projected EMA bundle on the instrument."""
    k.ctx.instrument("RELIANCE")._indicators.update({
        "ema_9": ema_fast, "ema_21": ema_slow,
        "rsi_14": 50.0, "atr_14": 1.0, "vwap": close,
    })
    event = CandleClosedEvent(
        symbol="RELIANCE", exchange="NSE", timeframe="5m",
        open=close, high=close, low=close, close=close, volume=100,
        ts=_TS + timedelta(minutes=5 * (i + 1)),
    )
    k.bus.publish(event)


def _fills(k):
    from ntrade.events.order import OrderFilledEvent
    return [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]


def test_golden_cross_buys_then_death_cross_sells():
    k = _kernel()
    k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5))
    # warm-up candle (no crossing yet)
    _candle(k, 100.0, 99.0, 100.0, 0)
    # golden cross: 9 EMA crosses above 21 EMA -> BUY
    _candle(k, 101.0, 100.5, 100.0, 1)
    # still above (no crossing) -> nothing
    _candle(k, 102.0, 101.0, 100.5, 2)
    # death cross -> SELL (exit the long)
    _candle(k, 101.0, 100.2, 100.4, 3)
    fills = _fills(k)
    assert [f.side for f in fills] == ["BUY", "SELL"]
    assert [f.quantity for f in fills] == [5, 5]
    assert k.ctx.portfolio.position("RELIANCE") is None  # exited flat


def test_does_not_stack_same_direction():
    k = _kernel()
    k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5))
    _candle(k, 100.0, 99.0, 100.0, 0)
    _candle(k, 101.0, 100.5, 100.0, 1)  # golden cross -> BUY (long 5)
    # another golden-cross shaped bar while already long must NOT add
    _candle(k, 103.0, 102.5, 101.5, 2)  # 9 was > 21 last bar, still > now
    # actual second golden cross while long -> no stack
    _candle(k, 104.0, 103.5, 103.0, 3)  # crossed 100.2>... reset state between
    fills = _fills(k)
    assert len(fills) == 1 and fills[0].side == "BUY"


def test_warmup_requires_prior_reading():
    k = _kernel()
    k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5))
    _candle(k, 101.0, 100.5, 100.0, 0)  # first reading, no prior -> no signal
    assert _fills(k) == []
    _candle(k, 102.0, 101.5, 101.0, 1)  # no cross (both rising) -> still nothing
    assert _fills(k) == []


def test_death_cross_while_short_does_not_stack():
    k = _kernel()
    k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5))
    _candle(k, 100.0, 101.0, 100.0, 0)
    _candle(k, 99.0, 99.5, 100.0, 1)  # death cross -> SELL (short 5)
    _candle(k, 98.0, 98.8, 99.2, 2)   # still below, no cross
    _candle(k, 97.0, 97.5, 98.0, 3)   # another death cross while short -> no stack
    fills = _fills(k)
    assert len(fills) == 1 and fills[0].side == "SELL"
    assert k.ctx.portfolio.position("RELIANCE").quantity == -5
