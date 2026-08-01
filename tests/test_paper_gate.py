"""Paper->live gate report builder (G2-E2)."""
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.gate import build_paper_report


def test_report_summary_from_kernel_history():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    report = build_paper_report(k, initial_cash=100_000.0)
    assert report["n_trades"] == 0
    assert report["final_equity"] == 100_000.0
    assert "checklist" in report
    assert "fills" in report and "max_drawdown_pct" in report
