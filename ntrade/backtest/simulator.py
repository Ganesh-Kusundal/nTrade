"""BacktestSimulator — run the standard kernel over historical OHLCV bars.

Zero parity: the exact same TradingKernel + engine stack + execution target
that live trading uses. The only differences are the event source (historical
bars), the clock (SimulationClock) and cost models (slippage/commission).

Each bar is published as a QuoteEvent (full market state) plus a TickEvent at
the close, so the market/candle/indicator/strategy engines behave exactly as
in live trading.
"""

from __future__ import annotations

import pandas as pd

from ntrade.backtest.fills import BarAwareExecution, FillPolicy
from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import QuoteEvent, TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.execution.costs import CommissionModel, SlippageModel
from ntrade.execution.router import ExecutionRouter
from ntrade.execution.simulator import SimulatedExecution
from ntrade.kernel.clock import SimulationClock
from ntrade.kernel.session import TradingKernel


class BacktestResult:
    def __init__(self, final_equity: float, total_return_pct: float, trades: list,
                 equity_curve: pd.DataFrame, n_trades: int,
                 commissions_total: float = 0.0, max_drawdown_pct: float = 0.0):
        self.final_equity = final_equity
        self.total_return_pct = total_return_pct
        self.trades = trades
        self.equity_curve = equity_curve
        self.n_trades = n_trades
        self.commissions_total = commissions_total
        self.max_drawdown_pct = max_drawdown_pct

    def __repr__(self) -> str:
        return (f"BacktestResult(final_equity={self.final_equity:,.2f}, "
                f"return={self.total_return_pct:.2f}%, trades={self.n_trades}, "
                f"commission={self.commissions_total:.2f}, "
                f"max_dd={self.max_drawdown_pct:.2f}%)")


class BacktestSimulator:
    def __init__(self, *, symbol: str = "NIFTY", exchange: str = "NSE",
                 timeframe: str = "1m", initial_cash: float = 100_000.0,
                 slippage: SlippageModel | None = None,
                 commission: CommissionModel | None = None,
                 fill_policy: FillPolicy | None = None,
                 clock: SimulationClock | None = None,
                 kernel: TradingKernel | None = None):
        self.symbol = symbol
        self.exchange = exchange
        self.timeframe = timeframe
        self.initial_cash = initial_cash
        self.slippage = slippage
        self.commission = commission
        self.fill_policy = fill_policy
        self.clock = clock or SimulationClock()
        self._curve_rows: list[tuple] = []
        self._current_bar = None
        if kernel is None:
            kernel = TradingKernel(
                mode="backtest", clock=self.clock, timeframe=timeframe,
                initial_cash=initial_cash,
            )
            kernel.register(Equity(symbol, exchange=exchange))
            router = ExecutionRouter(kernel.ctx)
            if fill_policy is not None:
                execution = BarAwareExecution(
                    kernel.ctx, policy=fill_policy,
                    bar_provider=lambda: self._current_bar,
                    slippage=slippage, commission=commission,
                )
            else:
                execution = SimulatedExecution(
                    kernel.ctx, slippage=slippage, commission=commission)
            router.add("default", execution)
            router.default("default")
            kernel.router = router
            kernel.order_engine.router = router
        self.kernel = kernel

    def register_strategy(self, strategy) -> "BacktestSimulator":
        self.kernel.register_strategy(strategy)
        return self

    # ------------------------------------------------------------------ run
    def run(self, data: pd.DataFrame) -> BacktestResult:
        """Run the kernel over an OHLCV frame (timestamp/open/high/low/close/volume)."""
        if data is None or data.empty:
            raise ValueError("data must be a non-empty OHLCV frame")
        self._curve_rows = []
        for _, row in data.iterrows():
            ts = row["timestamp"]
            self._current_bar = row  # for bar-aware limit fills
            self.clock.set(ts)
            close = float(row.get("close", 0))
            self.kernel.bus.publish(QuoteEvent(
                symbol=self.symbol, exchange=self.exchange, ltp=close, bid=0.0, ask=0.0,
                open=float(row.get("open", close)), high=float(row.get("high", close)),
                low=float(row.get("low", close)), volume=int(row.get("volume", 0) or 0), ts=ts,
            ))
            self.kernel.bus.publish(TickEvent(
                symbol=self.symbol, exchange=self.exchange, price=close,
                quantity=int(row.get("volume", 0) or 0), ts=ts,
            ))
            self._curve_rows.append((ts, self._mark_to_market(close)))
        self.kernel.stop(reason="backtest complete")
        return self.results()

    def _mark_to_market(self, close: float) -> float:
        position = self.kernel.ctx.portfolio.position(self.symbol)
        position_value = position.quantity * close if position else 0.0
        return round(self.kernel.ctx.account.balance + position_value, 2)

    # ------------------------------------------------------------------ results
    def results(self) -> BacktestResult:
        fills = [e for e in self.kernel.bus.history if isinstance(e, OrderFilledEvent)]
        trades = [{
            "order_id": f.order_id, "symbol": f.symbol, "side": f.side,
            "quantity": f.quantity, "fill_price": f.fill_price,
            "commission": f.commission, "ts": f.ts.isoformat(),
        } for f in fills]
        curve = pd.DataFrame(self._curve_rows, columns=["ts", "equity"])
        final = float(curve["equity"].iloc[-1]) if len(curve) else self.initial_cash
        commissions_total = round(sum(float(f.commission or 0.0) for f in fills), 4)
        if len(curve):
            peak = curve["equity"].cummax()
            drawdown = (curve["equity"] - peak) / peak
            # positive magnitude of the deepest trough
            max_drawdown_pct = round(abs(float(drawdown.min())) * 100, 4)
        else:
            max_drawdown_pct = 0.0
        return BacktestResult(
            final_equity=round(final, 2),
            total_return_pct=round((final - self.initial_cash) / self.initial_cash * 100, 2),
            trades=trades, equity_curve=curve, n_trades=len(trades),
            commissions_total=commissions_total, max_drawdown_pct=max_drawdown_pct,
        )
