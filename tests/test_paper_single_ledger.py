"""Paper cash must be a single ledger that charges costs exactly once.

Regression: PortfolioEngine debited notional+commission+statutory, then the
1 Hz PositionSyncEngine overwrote the kernel balance with PaperBroker's raw
notional-only balance — silently erasing every cost from paper equity.
"""
from datetime import datetime

from ntrade.events.market import TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.trading_session import TradingSession


class BuyOnce(Strategy):
    name = "buy_once"

    def __init__(self):
        super().__init__()
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=10, price=event.price,
                             reference_price=event.price)
            self.done = True


def _paper_kernel():
    """Real paper path (api/paper_trader.py): session factory injects the
    PaperBroker into instruments. Default statutory schedule — the cost
    pipeline under test."""
    session = TradingSession.paper(initial_cash=100_000.0, timeframe="1m",
                                   clock=ReplayClock())
    session.register(session.stock("NIFTY"))
    session.register_strategy(BuyOnce())
    return session


def test_paper_balance_charges_costs_exactly_once():
    session = _paper_kernel()
    k = session.kernel
    fills = []
    k.bus.subscribe(OrderFilledEvent, fills.append)
    session.start()
    for i in range(3):
        ts = datetime(2026, 8, 21, 9, 15 + i)
        k.clock.set(ts)
        k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0,
                                quantity=1, ts=ts))
    assert len(fills) == 1, f"expected one fill, got {len(fills)}"
    k.sync_positions()  # the 1 Hz snapshot loop's reconcile — must be a no-op on cash
    balance = k.ctx.account.balance
    assert balance < 100_000.0 - 10 * 100.0, (
        f"balance {balance} shows no cost charged beyond notional")
    # And it must be STABLE across repeated syncs (no drift):
    for _ in range(3):
        k.sync_positions()
    assert k.ctx.account.balance == balance


def test_paper_broker_declares_no_cash_authority():
    """PaperBroker reports a seeded constant, not real money — it must opt out
    of cash reconciliation explicitly."""
    from ntrade.brokers.paper import PaperBroker

    assert getattr(PaperBroker(), "reports_cash", True) is False
