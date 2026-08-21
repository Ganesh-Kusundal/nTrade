"""The daily-loss breaker must measure against the REAL account balance.

Regression: _start_balance froze at the kernel's constructor default (100k);
after PositionSyncEngine imported the real 5L balance, max_daily_loss was
measured against a fictional baseline — halting healthy accounts or never
halting dying ones.
"""
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.trading_session import TradingSession


class _CashReportingBroker:
    """Minimal broker that reports real cash (reports_cash=True by default)."""
    name = "cash_reporting"

    def __init__(self, balance=100_000.0):
        self._balance = balance

    def get_balance(self):
        return self._balance

    def get_positions(self):
        return []


def _session(broker):
    session = TradingSession(mode="paper", broker=broker, initial_cash=100_000.0,
                              timeframe="1m", clock=ReplayClock())
    session.register(session.stock("NIFTY"))
    return session


def test_first_sync_rebases_daily_loss_baseline():
    broker = _CashReportingBroker(balance=500_000.0)
    session = _session(broker)
    k = session.kernel
    risk = k.risk_engine
    risk.max_daily_loss = 50_000.0
    k.sync_positions()
    assert risk._start_balance == 500_000.0


def test_rebase_happens_only_once():
    """Later syncs must NOT re-arm the loss budget — a mid-session drawdown
    must keep counting against the original baseline."""
    broker = _CashReportingBroker(balance=500_000.0)
    session = _session(broker)
    k = session.kernel
    risk = k.risk_engine
    k.sync_positions()
    first = risk._start_balance
    # A later, different balance (e.g. after a losing day) must not re-base.
    broker._balance = 450_000.0
    k.sync_positions()
    assert risk._start_balance == first


def test_resume_rebases_again():
    """Only an explicit resume() re-arms the budget with the current balance."""
    broker = _CashReportingBroker(balance=500_000.0)
    session = _session(broker)
    k = session.kernel
    risk = k.risk_engine
    k.sync_positions()
    risk.resume()
    assert risk._start_balance == float(k.ctx.account.balance)


def test_paper_sync_never_rebases():
    """reports_cash=False brokers (paper) don't import cash — no re-base."""
    from ntrade.brokers.paper import PaperBroker

    session = TradingSession(mode="paper", broker=PaperBroker(),
                              initial_cash=100_000.0, timeframe="1m",
                              clock=ReplayClock())
    session.register(session.stock("NIFTY"))
    k = session.kernel
    risk = k.risk_engine
    baseline = risk._start_balance
    k.broker._balance = 500_000.0  # paper's seeded constant; ignored by sync
    k.sync_positions()
    assert risk._start_balance == baseline


def test_rebase_method_guards_double_call():
    from ntrade.engines.risk_engine import RiskEngine

    class _Bus:
        def subscribe(self, *a):
            pass

    ctx = type("C", (), {"bus": _Bus(),
                         "account": type("A", (), {"balance": 100.0})(),
                         "portfolio": type("P", (), {"positions": []})(),
                         "now": lambda: None})()
    risk = RiskEngine(ctx)
    risk.rebase(500.0)
    assert risk._start_balance == 500.0
    risk.rebase(999.0)  # second call is a no-op
    assert risk._start_balance == 500.0
