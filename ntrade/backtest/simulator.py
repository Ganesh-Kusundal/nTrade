"""BacktestSimulator — run the standard kernel over historical OHLCV bars.

Zero parity: the exact same TradingKernel + engine stack + execution target
that live trading uses. The only differences are the event source (historical
bars), the clock (SimulationClock) and cost models (slippage/commission).

Each bar is published as a QuoteEvent (full market state) plus a TickEvent at
the close, so the market/candle/indicator/strategy engines behave exactly as
in live trading.

LIMIT fills are bar-aware by default: a limit order only fills when a bar
trades through it (FillPolicy), mirroring live behaviour — it never fills at
its limit price against a bar that never reached it. Pass ``fill_policy`` to
override the default policy.
"""

from __future__ import annotations

import pandas as pd

from ntrade.backtest.fills import BarAwareExecution, FillPolicy
from ntrade.domain.constants import DEFAULT_INITIAL_CASH, DEFAULT_TIMEFRAME
from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import DepthEvent, QuoteEvent, TickEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.sim.depth_simulator import (
    DEFAULT_TICK_SIZE, bar_imbalance, depth_to_wire, synthesize_depth,
)
from ntrade.execution.costs import (
    CommissionModel, FuturesCarryCosts, SlippageModel, STATUTORY_DEFAULT,
)
from ntrade.execution.router import ExecutionRouter
from ntrade.kernel.clock import SimulationClock
from ntrade.kernel.session import TradingKernel


class BacktestResult:
    def __init__(self, final_equity: float, total_return_pct: float, trades: list,
                 equity_curve: pd.DataFrame, n_trades: int,
                 commissions_total: float = 0.0, statutory_total: float = 0.0,
                 max_drawdown_pct: float = 0.0, futures_costs_total: float = 0.0):
        self.final_equity = final_equity
        self.total_return_pct = total_return_pct
        self.trades = trades
        self.equity_curve = equity_curve
        self.n_trades = n_trades
        self.commissions_total = commissions_total
        self.statutory_total = statutory_total
        self.max_drawdown_pct = max_drawdown_pct
        self.futures_costs_total = futures_costs_total

    @property
    def costs_total(self) -> float:
        """All charges deducted from PnL: commission + statutory + futures costs."""
        return round(self.commissions_total + self.statutory_total + self.futures_costs_total, 4)

    def __repr__(self) -> str:
        return (f"BacktestResult(final_equity={self.final_equity:,.2f}, "
                f"return={self.total_return_pct:.2f}%, trades={self.n_trades}, "
                f"commission={self.commissions_total:.2f}, "
                f"statutory={self.statutory_total:.2f}, "
                f"futures={self.futures_costs_total:.2f}, "
                f"max_dd={self.max_drawdown_pct:.2f}%)")


class BacktestSimulator:
    def __init__(self, *, symbol: str = "NIFTY", exchange: str = "NSE",
                  timeframe: str = DEFAULT_TIMEFRAME, initial_cash: float = DEFAULT_INITIAL_CASH,
                 slippage: SlippageModel | None = None,
                 commission: CommissionModel | None = None,
                 statutory=STATUTORY_DEFAULT,
                 futures_costs: "FuturesCarryCosts | None" = None,
                 fill_policy: FillPolicy | None = None,
                 clock: SimulationClock | None = None,
                 kernel: TradingKernel | None = None,
                 instrument=None,
                 delivery_detection: bool = True,
                 depth_levels: int = 0, depth_imbalance: float = 0.0,
                 depth_imbalance_mode: str = "constant",
                 depth_seed: int = 0, depth_tick_size: float = DEFAULT_TICK_SIZE,
                 risk_kwargs: dict | None = None):
        self.symbol = symbol
        self.exchange = exchange
        self.timeframe = timeframe
        self.initial_cash = initial_cash
        self.slippage = slippage
        self.commission = commission
        self.statutory = statutory
        self.futures_costs = futures_costs
        self.delivery_detection = delivery_detection
        # Opt-in simulated order book (Valentini depth filter): >0 levels
        # publish one DepthEvent per bar. Default 0 keeps the book empty ->
        # zero-parity with the pre-depth simulation behaviour.
        self.depth_levels = max(0, int(depth_levels))
        self.depth_imbalance = depth_imbalance
        self.depth_imbalance_mode = depth_imbalance_mode
        self.depth_seed = depth_seed
        self.depth_tick_size = depth_tick_size
        self.depth_events_published = 0
        # Effective policy: None means the default bar-aware FillPolicy.
        self.fill_policy = fill_policy or FillPolicy()
        self.risk_kwargs = risk_kwargs or {}
        self.clock = clock or SimulationClock()
        self._curve_rows: list[tuple] = []
        self._current_bar = None
        self._last_carry_date = None
        self._rolled: set[str] = set()
        self._futures_costs_total = 0.0
        self._fills: list[OrderFilledEvent] = []
        if kernel is None:
            kernel = TradingKernel(
                mode="backtest", clock=self.clock, timeframe=timeframe,
                initial_cash=initial_cash,
            )
            kernel.register(instrument or Equity(symbol, exchange=exchange))
            router = ExecutionRouter(kernel.ctx)
            # Bar-aware LIMIT fills are the default (a limit only fills when a
            # bar trades through it); pass fill_policy for a custom policy.
            execution = BarAwareExecution(
                kernel.ctx, policy=self.fill_policy,
                bar_provider=lambda: self._current_bar,
                slippage=slippage, commission=commission, statutory=statutory,
                delivery_detection=delivery_detection,
            )
            router.add("default", execution)
            router.default("default")
            kernel.router = router
            kernel.order_engine.router = router
        # Apply risk limits to the kernel's RiskEngine (ponytail: the kernel
        # creates a default RiskEngine with no limits; backtest callers must
        # inject caps so breakers are evaluated during the run loop).
        for key, val in self.risk_kwargs.items():
            if hasattr(kernel.risk_engine, key):
                setattr(kernel.risk_engine, key, val)
        self.kernel = kernel
        # Record every fill in an unbounded list — the bus history is capped at
        # 10k events, so results() cannot trust it for long runs.
        self.kernel.bus.subscribe(OrderFilledEvent, self._on_fill)

    def _on_fill(self, event: OrderFilledEvent) -> None:
        """Record every fill in an unbounded list (the bus history is capped
        at 10k events, so results() cannot trust it for long runs)."""
        self._fills.append(event)

    def register_strategy(self, strategy) -> "BacktestSimulator":
        self.kernel.register_strategy(strategy)
        return self

    # ------------------------------------------------------------------ run
    def run(self, data: pd.DataFrame) -> BacktestResult:
        """Run the kernel over an OHLCV frame (timestamp/open/high/low/close/volume)."""
        if data is None or data.empty:
            raise ValueError("data must be a non-empty OHLCV frame")
        self._curve_rows = []
        self._last_carry_date = None
        self._rolled = set()
        self._futures_costs_total = 0.0
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
            if self.depth_levels > 0:
                imbalance = self.depth_imbalance
                if self.depth_imbalance_mode == "bar":
                    imbalance = bar_imbalance(
                        float(row.get("open", close)), close,
                        scale=abs(imbalance) or 0.5)
                bids, asks = synthesize_depth(
                    close, tick_size=self.depth_tick_size,
                    levels=self.depth_levels, imbalance=imbalance,
                    seed=self.depth_seed,
                )
                wbids, wasks = depth_to_wire(bids, asks)
                self.kernel.bus.publish(DepthEvent(
                    symbol=self.symbol, exchange=self.exchange,
                    bids=wbids, asks=wasks, ts=ts,
                ))
                self.depth_events_published += 1
            # Close the candle for this bar NOW (before the next bar's
            # QuoteEvent overwrites the instrument's _quote.ltp). In live and
            # replay, the CandleClosed fires when the first tick of the NEXT
            # bar arrives — the market still shows the current bar's last
            # price at that moment. Backtest must mirror this: flush the
            # candle so the strategy's MARKET fill reads ltp = bar close
            # (the tick set it), not the next bar's close. Without this,
            # backtest fills at the NEXT bar's close while replay fills at
            # the current bar's close — a zero-parity violation.
            self.kernel.candle_engine.flush(self.symbol)
            # Futures holding-period costs accrue after the bar's state is in
            # place (positions updated by the fills this bar published).
            self._apply_futures_costs(ts)
            # Evaluate risk circuit breakers every bar — without LiveRunner,
            # the breakers would only update on signal arrival, missing
            # price-driven losses on bars with no signal (zero-parity gap).
            self.kernel.risk_engine.check()
            self._curve_rows.append((ts, self._mark_to_market(close)))
        self.kernel.stop(reason="backtest complete")
        return self.results()

    def _apply_futures_costs(self, ts) -> None:
        """Accrue daily carry and expiry roll slippage on open futures positions.

        Opt-in via ``futures_costs``. Carry (contango drag) accrues on the
        holding notional between session dates; a held contract that crosses
        its expiry pays the one-off roll cost (charged once per contract).
        Charges are deducted from the account balance so the equity curve and
        reported totals stay consistent with the fills pipeline. Notional uses
        the position's recorded price (entry/fill, since ``Position.ltp`` only
        updates on fills), so carry/roll are charged on entry notional — a
        deterministic approximation of the holding-period contract value.
        """
        if self.futures_costs is None:
            return
        from ntrade.domain.instruments.derivatives import Future

        today = ts.date()
        for pos in list(self.kernel.ctx.portfolio.positions):
            inst = self.kernel.ctx.instrument(pos.symbol)
            if inst is None or not isinstance(inst, Future):
                continue
            notional = (pos.avg_price or pos.ltp) * abs(pos.quantity)
            # 1. expiry rollover slippage — once per contract, when held past expiry
            if inst.expiry is not None and today > inst.expiry and pos.symbol not in self._rolled:
                self._rolled.add(pos.symbol)
                self._deduct_futures(self.futures_costs.roll_cost(notional))
            # 2. daily carry (roll yield) — longs pay, shorts receive
            if self._last_carry_date is not None and today > self._last_carry_date:
                days = (today - self._last_carry_date).days
                if self.futures_costs.within_window(inst.expiry, today):
                    carry = self.futures_costs.daily_carry(notional, days)
                    if pos.quantity < 0:
                        carry = -carry
                    self._deduct_futures(carry)
        self._last_carry_date = today

    def _deduct_futures(self, amount: float) -> None:
        if not amount:
            return
        self._futures_costs_total = round(self._futures_costs_total + abs(amount), 4)
        self.kernel.ctx.account.balance = round(
            self.kernel.ctx.account.balance - amount, 4)

    def _mark_to_market(self, close: float) -> float:
        position = self.kernel.ctx.portfolio.position(self.symbol)
        if position is not None:
            position.ltp = close  # sync ltp so RiskEngine.equity() sees MTM
        position_value = position.quantity * close if position else 0.0
        return round(self.kernel.ctx.account.balance + position_value, 2)

    # ------------------------------------------------------------------ results
    def results(self, *, include_halftrend_markers: bool = False) -> BacktestResult:
        fills = self._fills
        trades = [{
            "order_id": f.order_id, "symbol": f.symbol, "side": f.side,
            "quantity": f.quantity, "fill_price": f.fill_price,
            "commission": f.commission, "statutory": f.statutory, "ts": f.ts.isoformat(),
        } for f in fills]
        if include_halftrend_markers:
            try:
                from ntrade.domain.analytics.halftrend import halftrend as _ht
                inst = self.kernel.ctx.instrument(self.symbol)
                series = inst._indicators.get("halftrend_series")
                if series is not None and not series.empty:
                    last = series.iloc[-1]
                    for t in trades:
                        t["halftrend_ht"] = None
                        t["halftrend_trend"] = int(last.get("trend", 0)) if pd.notna(last.get("trend")) else None
                        if last.get("buySignal"):
                            t["halftrend_signal"] = "BUY"
                        elif last.get("sellSignal"):
                            t["halftrend_signal"] = "SELL"
            except Exception:  # noqa: BLE001 — marker sourcing is non-fatal
                pass
        curve = pd.DataFrame(self._curve_rows, columns=["ts", "equity"])
        final = float(curve["equity"].iloc[-1]) if len(curve) else self.initial_cash
        commissions_total = round(sum(float(f.commission or 0.0) for f in fills), 4)
        statutory_total = round(sum(float(getattr(f, "statutory", 0.0) or 0.0) for f in fills), 4)
        futures_costs_total = self._futures_costs_total
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
            commissions_total=commissions_total, statutory_total=statutory_total,
            futures_costs_total=futures_costs_total, max_drawdown_pct=max_drawdown_pct,
        )

    def run_halftrend(self, data: pd.DataFrame,
                      amplitude: int = 2, channel_deviation: int = 2,
                      atr_period: int = 100) -> BacktestResult:
        """Run a HalfTrend-only backtest: indicators computed in-sim, signals
        replayed via the same kernel path as the chart overlay."""
        from ntrade.domain.analytics.halftrend import halftrend as _ht
        inst = self.kernel.ctx.instrument(self.symbol)
        bundle = _ht(data, amplitude=amplitude,
                     channel_deviation=channel_deviation,
                     atr_period=atr_period)
        if not bundle.empty and pd.notna(bundle["ht"].iloc[-1]):
            inst._indicators["halftrend_ht"] = float(bundle["ht"].iloc[-1])
        from ntrade.engines.strategies import HalfTrendStrategy
        self.register_strategy(HalfTrendStrategy(
            symbol=self.symbol, amplitude=amplitude,
            channel_deviation=channel_deviation, atr_period=atr_period))
        if not bundle.empty:
            inst._indicators["halftrend_series"] = bundle
        return self.run(data)
