"""EMA 9/21 crossover through the kernel flow, on real historical data.

Flow exercised:
  live Dhan fetch (5m OHLCV) → SimulatedFeedSource → EventBus
    → MarketEngine (quote read-model) → CandleEngine (CandleClosed)
    → IndicatorEngine (ema_9/ema_21 bundle) → EmaCrossStrategy
    → RiskEngine (SignalApproved) → OrderEngine → SimulatedExecution
    → PortfolioEngine (PositionUpdated + BalanceChanged)

Usage:  .venv/bin/python scripts/ema_cross_run.py [SYMBOL=NIFTY] [DAYS=15]
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.domain.instruments.cash import Index  # noqa: E402
from ntrade.engines.strategies import EmaCrossStrategy  # noqa: E402
from ntrade.events.order import OrderFilledEvent  # noqa: E402
from ntrade.kernel.clock import ReplayClock  # noqa: E402
from ntrade.kernel.session import TradingKernel  # noqa: E402
from ntrade.sources.market_feed import SimulatedFeedSource  # noqa: E402


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    # 1) live historical data through the framework
    from ntrade.kernel.trading_session import TradingSession
    session = TradingSession.connect("dhan")
    instrument = session.index(symbol)
    series = instrument.history("5m", days=days, force=True)
    frame = series.df
    print(f"fetched {len(frame)} x 5m candles for {symbol} ({days}d)")

    # 2) kernel = identical stack, replay mode
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="5m",
                      initial_cash=100_000.0)
    k.register(Index(symbol))
    k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol=symbol))
    k.start()

    # 3) feed the historical frame as canonical events
    source = SimulatedFeedSource(k, symbol=symbol, exchange="NSE", data=frame)
    source.start()
    k.candle_engine.flush()  # close the final partial candle
    k.stop(reason="end of historical data")

    # 4) report the flow
    kinds = Counter(type(e).__name__ for e in k.bus.history)
    print("\n== event flow (counts) ==")
    for kind in ("TickEvent", "QuoteEvent", "QuoteUpdatedEvent", "CandleClosedEvent",
                 "IndicatorUpdatedEvent", "SignalGeneratedEvent", "SignalApprovedEvent",
                 "OrderIntentEvent", "OrderAcceptedEvent", "OrderFilledEvent",
                 "PositionUpdatedEvent", "BalanceChangedEvent"):
        if kind in kinds:
            print(f"  {kind:<24} {kinds[kind]}")

    fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
    print(f"\n== trades ({len(fills)} fills) ==")
    for f in fills:
        print(f"  {f.ts:%Y-%m-%d %H:%M}  {f.side:<4} {f.quantity:>3} @ {f.fill_price:>10.2f}")

    pos = k.ctx.portfolio.position(symbol)
    print(f"\n== final state ==")
    if pos is not None:
        print(f"  position: {pos.quantity:+d} {symbol} @ avg {pos.avg_price:.2f}")
    else:
        print(f"  position: flat")
    print(f"  balance : {k.ctx.account.balance:,.2f}")
    # mark-to-market equity: the open position's shares are valued at last price
    mtm = 0.0
    for p in k.ctx.portfolio.positions:
        mtm += p.quantity * (p.ltp or p.avg_price)
    equity = k.ctx.account.balance + mtm
    print(f"  equity  : {equity:,.2f}  (balance {k.ctx.account.balance:,.2f} "
          f"{'+' if mtm >= 0 else ''}{mtm:,.2f} open-position MTM)")
    print(f"  P&L     : {equity - 100_000.0:+,.2f}  (mark-to-market)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
