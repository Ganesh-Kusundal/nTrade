"""RiskEngine circuit breakers (G2-C1)."""
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.portfolio import Position
from ntrade.events.market import TickEvent
from ntrade.events.risk import RiskHaltedEvent, RiskResumedEvent, SignalGeneratedEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _kernel(initial_cash=100_000.0, **risk_kw):
    from ntrade.events.risk import SignalGeneratedEvent
    from ntrade.engines.risk_engine import RiskEngine
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=initial_cash)
    k.register(Equity("NIFTY"))
    # detach the kernel's default engine so signals are screened only once
    k.bus.unsubscribe(SignalGeneratedEvent, k.risk_engine.on_signal)
    k.risk_engine = RiskEngine(k.ctx, **risk_kw)
    return k


def _signal(k, price=100.0, qty=1, side="BUY"):
    return SignalGeneratedEvent(symbol="NIFTY", exchange="NSE", side=side,
                                quantity=qty, price=price, strategy="t",
                                ts=k.clock.now())


def test_daily_loss_halts_and_rejects_all():
    k = _kernel(initial_cash=10_000.0, max_daily_loss=500.0)
    # a losing short position (MTM loss = -1500): equity = 10_000 - 1500
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    k.risk_engine.on_signal(_signal(k))       # trips the breaker
    k.risk_engine.on_signal(_signal(k))       # a second signal is rejected too
    assert k.risk_engine.halted
    assert "daily loss" in k.risk_engine.halt_reason
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    assert len(rejects) == 2


def test_drawdown_halts():
    k = _kernel(initial_cash=10_000.0, max_drawdown_pct=10.0)
    # peak equity is captured at 10_000 on the first (clean) signal
    k.risk_engine.on_signal(_signal(k))
    assert not k.risk_engine.halted
    # now a large losing short position pushes equity to 6_500 (-35% drawdown)
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=450.0))
    k.risk_engine.on_signal(_signal(k))
    assert k.risk_engine.halted
    assert "drawdown" in k.risk_engine.halt_reason


def test_price_deviation_rejects_but_does_not_halt():
    k = _kernel(price_deviation_pct=1.0)
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=k.clock.now()))
    k.risk_engine.on_signal(_signal(k, price=105.0))  # 5% off ltp
    k.risk_engine.on_signal(_signal(k, price=100.5))  # 0.5% off -> approved
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(rejects) == 1
    assert len(approves) == 1
    assert not k.risk_engine.halted


def test_halt_and_resume_emit_events_and_restore():
    k = _kernel(max_daily_loss=1.0)
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    k.risk_engine.on_signal(_signal(k))
    assert k.risk_engine.halted
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)
    k.risk_engine.resume()
    assert not k.risk_engine.halted
    assert any(isinstance(e, RiskResumedEvent) for e in k.bus.history)


def test_no_breakers_means_no_halting():
    k = _kernel()
    k.ctx.portfolio.positions.append(Position("NIFTY", 10, avg_price=100.0, ltp=1.0))
    k.risk_engine.on_signal(_signal(k))
    assert not k.risk_engine.halted
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(approves) == 1


def test_check_evaluates_breakers_without_a_signal():
    k = _kernel(initial_cash=10_000.0, max_daily_loss=500.0)
    assert not k.risk_engine.halted
    # no signal is ever emitted — equity is eroded by a bleeding position
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    reason = k.risk_engine.check()
    assert k.risk_engine.halted
    assert reason and "daily loss" in reason
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)
    # idempotent: a second check() reports the same halt, no re-publish
    n = len([e for e in k.bus.history if isinstance(e, RiskHaltedEvent)])
    assert k.risk_engine.check() == reason
    assert len([e for e in k.bus.history if isinstance(e, RiskHaltedEvent)]) == n


def test_price_deviation_rejects_unverifiable_price():
    k = _kernel(price_deviation_pct=1.0)
    # no tick/quote was ever published -> instrument ltp stays 0.0
    k.risk_engine.on_signal(_signal(k, price=100.0))
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(rejects) == 1
    assert "unverifiable" in rejects[0].reason
    assert approves == []
    assert not k.risk_engine.halted  # a rejected signal is not a halt


def test_market_signal_notional_uses_live_ltp():
    """A MARKET signal (price=0) must not bypass max_notional — the cap
    estimates notional from the instrument's live LTP."""
    k = _kernel(max_notional=50_000.0)
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=k.clock.now()))
    # price=0 (market): 0 * 600 would be 0 -> previously passed the cap
    k.risk_engine.on_signal(_signal(k, price=0.0, qty=600))
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(rejects) == 1
    assert "notional 60000.00 exceeds max 50000.0" in rejects[0].reason
    assert approves == []

    # same engine: a small market order is approved (notional 100*100)
    k.risk_engine.on_signal(_signal(k, price=0.0, qty=100))
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(approves) == 1


def test_market_signal_notional_falls_back_to_prev_close():
    """Without a live tick, the notional estimate falls back to prev_close."""
    k = _kernel(max_notional=50_000.0)
    k.ctx.instrument("NIFTY")._quote = k.ctx.instrument("NIFTY")._quote.with_update(prev_close=250.0)
    k.risk_engine.on_signal(_signal(k, price=0.0, qty=300))  # 250*300 = 75_000
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    assert len(rejects) == 1
    assert "notional 75000.00 exceeds max 50000.0" in rejects[0].reason
