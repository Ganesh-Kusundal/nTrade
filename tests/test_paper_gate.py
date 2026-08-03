"""Paper->live gate report builder (G2-E2)."""
from datetime import datetime, timedelta

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.order import OrderFilledEvent, OrderIntentEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.gate import _equity_trace, build_paper_report


def test_report_summary_from_kernel_history():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    report = build_paper_report(k, initial_cash=100_000.0)
    assert report["n_trades"] == 0
    assert report["final_equity"] == 100_000.0
    assert "checklist" in report
    assert "fills" in report and "max_drawdown_pct" in report
    # empty run → zero charges in the checklist
    assert report["checklist"]["total_charges"] == 0.0


def test_report_surfaces_per_fill_charges_and_total():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    k.register(Equity("RELIANCE", exchange="NSE"))
    ts = datetime(2026, 1, 1, 9, 15)
    k.router.submit(OrderIntentEvent(
        symbol="RELIANCE", exchange="NSE", side="BUY", quantity=10,
        order_type="LIMIT", price=100.0, strategy="g", ts=ts))
    report = build_paper_report(k, initial_cash=100_000.0)
    fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
    assert report["n_trades"] == 1
    trade = report["fills"][0]
    assert trade["commission"] == fills[0].commission
    assert trade["statutory"] == fills[0].statutory
    assert fills[0].statutory > 0.0  # default statutory wiring on the sim target
    expected = round(fills[0].commission + fills[0].statutory, 2)
    assert report["checklist"]["total_charges"] == expected


def test_equity_trace_converges_on_portfolio_read_model():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    k.register(Equity("RELIANCE", exchange="NSE"))
    ts = datetime(2026, 1, 1, 9, 15)
    k.router.submit(OrderIntentEvent(
        symbol="RELIANCE", exchange="NSE", side="BUY", quantity=10,
        order_type="LIMIT", price=100.0, strategy="g", ts=ts))
    report = build_paper_report(k, initial_cash=100_000.0)
    # The gate's equity must converge on the portfolio read model
    # (RiskEngine.equity = account.balance + Σ Position.market_value).
    assert report["final_equity"] == pytest.approx(k.risk_engine.equity(), abs=0.01)
    trace = list(_equity_trace(k, initial_cash=100_000.0))
    peak, final_eq = trace[-1]
    # A single BUY fill at 100.0 with no subsequent price move must not spike:
    # the position is marked at fill price and cash is debited at the same
    # time, so no intermediate equity may exceed the pre-fill cash, and the
    # settled value must match RiskEngine.equity exactly.
    assert peak == pytest.approx(100_000.0, abs=0.01)
    assert final_eq == pytest.approx(k.risk_engine.equity(), abs=0.01)
    assert report["max_drawdown_pct"] == pytest.approx(0.0, abs=0.01)


def test_equity_trace_drops_closed_positions():
    """K-026: a close (quantity=0 PositionUpdatedEvent) must REMOVE the symbol
    from the trace — a stale entry would mark a closed position at its last
    ltp and inflate equity. Round-trip a BUY then a SELL-to-flat at the same
    price: final equity must equal the initial cash (minus charges), with no
    phantom position contributing to the mark-to-market."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    k.register(Equity("RELIANCE", exchange="NSE"))
    ts = datetime(2026, 1, 1, 9, 15)
    k.router.submit(OrderIntentEvent(
        symbol="RELIANCE", exchange="NSE", side="BUY", quantity=10,
        order_type="LIMIT", price=100.0, strategy="g", ts=ts))
    k.router.submit(OrderIntentEvent(
        symbol="RELIANCE", exchange="NSE", side="SELL", quantity=10,
        order_type="LIMIT", price=100.0, strategy="g", ts=ts + timedelta(seconds=1)))
    report = build_paper_report(k, initial_cash=100_000.0)
    trace = list(_equity_trace(k, initial_cash=100_000.0))
    peak, final_eq = trace[-1]
    # Flat at the same price: no position value remains in the trace, so the
    # settled equity is exactly initial cash minus both legs' charges.
    charges = report["checklist"]["total_charges"]
    assert final_eq == pytest.approx(100_000.0 - charges, abs=0.01)
    assert final_eq == pytest.approx(k.risk_engine.equity(), abs=0.01)
    # Flat round-trip never spikes equity above the initial cash — a phantom
    # stale position would push peak past 100_000 (K-026 regression guard).
    assert peak == pytest.approx(100_000.0, abs=0.01)
