#!/usr/bin/env python3
"""ValentiniScalper — live / paper session wiring (plan Task 7, Phase 7).

Resolves the front-month NIFTY future, registers ``ValentiniScalper`` with
full risk caps, and drives the day through the zero-parity kernel via
``LiveRunner``:

  synth mode (default):  real 1m NIFTY future history replayed as 1s ticks —
      full pipeline rehearsal (feed -> candles -> strategy -> risk -> paper
      execution) with NO real orders. The Dhan connection is used ONLY to
      resolve the future + pull history, then closed before the run — the
      kernel is a paper session, so no live BrokerExecution exists. Pass
      ``--csv path.csv`` to replay a local 1m OHLCV file instead (fully
      offline, no Dhan needed).
  live mode:             real Dhan websocket feed (NFO Full mode). The broker
      is the real DhanBroker — a signal here becomes a REAL order. Default
      risk caps are deliberately tight; review them before ``--feed live``.

Usage:
  ./.venv/bin/python scripts/live_valentini.py --days 3 --duration 20
  ./.venv/bin/python scripts/live_valentini.py --csv /tmp/nifty_1m.csv --duration 20
  ./.venv/bin/python scripts/live_valentini.py --feed live --duration 60 \
      --max-quantity 25 --max-daily-loss 5000

On ``RiskHaltedEvent`` the LiveRunner trips the broker kill switch and the
BrokerExecution circuit breaker (flattens + rejects new orders).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# The strategy's own skip/expire logs are debug-noise for a smoke run.
logging.getLogger("ntrade.strategy.valentini").setLevel(logging.WARNING)

from ntrade.domain.constants import Exchange  # noqa: E402
from ntrade.engines.strategies import ValentiniScalper  # noqa: E402
from ntrade.kernel.trading_session import TradingSession  # noqa: E402
from ntrade.runner.feeds import build_source  # noqa: E402
from ntrade.runner.live_runner import LiveRunner  # noqa: E402


def _front_month_future(session):
    """Resolve the front-month NIFTY future (expiry index 0)."""
    nifty = session.index("NIFTY")
    expiries = session.broker.get_expiry_date(nifty, opt_fut="FUTURE")
    if not expiries:
        raise RuntimeError("no NIFTY future expiries resolved from Dhan")
    fut = session.future(nifty, expiry=expiries[0])
    return fut


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--feed", choices=("synth", "live"), default="synth",
                   help="synth replays real history offline; live uses the real websocket")
    p.add_argument("--csv", default="",
                   help="replay a local 1m OHLCV CSV (offline, no Dhan); implies synth")
    p.add_argument("--days", type=int, default=3, help="days of 1m history (synth feed)")
    p.add_argument("--duration", type=float, default=20.0, help="run seconds")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--sync", type=float, default=15.0)
    # strategy knobs
    p.add_argument("--range-size", type=float, default=0.0, help="0 = ATR auto")
    p.add_argument("--risk-pct", type=float, default=0.5)
    p.add_argument("--tp-mult", type=float, default=2.0)
    p.add_argument("--min-rr", type=float, default=1.5)
    p.add_argument("--depth-min", type=float, default=0.0,
                   help=">0 enables the depth-imbalance confidence filter")
    p.add_argument("--depth-levels", type=int, default=0,
                   help=">0 simulates an order book in synth mode (depth_imbalance_min now gates)")
    p.add_argument("--depth-imbalance", type=float, default=0.0,
                   help="buy-pressure bias of the simulated book (-1..1; positive = buy)")
    p.add_argument("--depth-mode", choices=("constant", "bar"), default="constant",
                   help="'bar' derives per-bar pressure from each bar's move (filter discriminates setups)")
    p.add_argument("--depth-seed", type=int, default=0, help="seeded book determinism")
    # risk caps (per-strategy RiskEngine). NOTE: the strategy already sizes
    # each position to --risk-pct of balance, so these are SANITY CEILINGS,
    # not the sizing control. NIFTY futures sit near 25,000 points: a 0.5%
    # risk budget over a ~10pt stop sizes ~540 contracts -> ~13M notional, so
    # the old 500 / 2M defaults rejected every signal. Raise --risk-pct to
    # trade smaller; the caps below only catch runaway sizing.
    p.add_argument("--max-quantity", type=int, default=10_000)
    p.add_argument("--max-notional", type=float, default=200_000_000.0)
    p.add_argument("--max-daily-loss", type=float, default=50_000.0)
    p.add_argument("--max-drawdown-pct", type=float, default=3.0)
    p.add_argument("--live-kwargs", default="{}", help="JSON kwargs for the live feed")
    args = p.parse_args()

    offline = bool(args.csv)
    live = args.feed == "live"
    if offline and live:
        raise SystemExit("--csv replays a local file and implies synth; "
                         "it cannot be combined with --feed live")
    session = None
    expiry = None
    frame = None
    symbol = ""
    if offline:
        import pandas as pd
        frame = pd.read_csv(args.csv, parse_dates=["timestamp"])
        symbol = str(frame.get("symbol", pd.Series("NIFTY FUT")).iloc[0])
        print(f"offline    : {len(frame)} x 1m bars from {args.csv} (symbol {symbol})")
        print(f"instrument : {symbol} (no broker — CSV replay)")
    else:
        # Resolve the front-month future (+ history for synth) through a Dhan
        # connection. Synth mode must NEVER carry a live broker into the
        # kernel (BrokerExecution would place REAL orders), so it uses a
        # throwaway connection closed before the run. Only --feed live keeps
        # the Dhan session alive and reuses it as the run session.
        session = TradingSession.connect("dhan")
        try:
            fut = _front_month_future(session)
            symbol = fut.symbol
            expiry = fut.expiry
            if not live:
                series = session.broker.get_historical(fut, timeframe="1m", days=args.days)
                frame = series.to_dataframe() if hasattr(series, "to_dataframe") else series
                frame = frame.copy()
            else:
                session.register(fut)
        finally:
            if not live and session.broker is not None:
                if hasattr(session.broker, "stop"):
                    session.broker.stop()
                session.disconnect()
        print(f"instrument : {symbol}  expiry {expiry}")
        if not live:
            print(f"synth feed : {len(frame)} x 1m bars over {args.days}d")
    try:
        if not live:
            session = TradingSession.paper(initial_cash=1_000_000.0)
            if offline:
                # stock() attaches the paper broker so signals fill (paper OMS).
                inst = session.stock(symbol)
                # PaperBroker fills at the instrument's quote; seed it from the
                # CSV's first close so paper prices match the replayed market
                # (no 100.0 default). Ticks overwrite it as the replay advances.
                if hasattr(session.broker, "seed_quote"):
                    session.broker.seed_quote(symbol, float(frame.iloc[0]["close"]))
                session.register(inst)
            else:
                # Synth runs on the paper session too — same symbol/expiry as
                # the resolved future, but bound to the PaperBroker so fills
                # are paper. NO live broker in the kernel.
                session.register(session.future(
                    session.index("NIFTY"), expiry=expiry, symbol=symbol))

        # Per-strategy RiskEngine caps via the StrategyRunner.
        import json
        session.register_strategy(ValentiniScalper(
            symbol=symbol, exchange=Exchange.DERIVATIVES if live else Exchange.CASH,
            range_size=args.range_size or None,
            risk_per_trade_pct=args.risk_pct,
            tp_multiplier=args.tp_mult,
            min_rr=args.min_rr,
            depth_imbalance_min=args.depth_min or None,
        ), risk={
            "max_quantity": args.max_quantity,
            "max_notional": args.max_notional,
            "max_daily_loss": args.max_daily_loss,
            "max_drawdown_pct": args.max_drawdown_pct,
        })
        print(f"risk caps  : qty<={args.max_quantity} notional<={args.max_notional:,.0f} "
              f"daily-loss<={args.max_daily_loss:,.0f} dd<={args.max_drawdown_pct}%")
        if not live and args.depth_levels > 0:
            print(f"depth sim  : {args.depth_levels} levels, mode={args.depth_mode}, "
                  f"imbalance={args.depth_imbalance:+.2f} "
                  f"(filter min {args.depth_min or 0:+.2f})")

        live_kwargs = json.loads(args.live_kwargs)
        if live and not live_kwargs:
            raise SystemExit(
                "live feed requires --live-kwargs with symbols/symbol_map for the "
                "front-month future (see scripts/live_runner_run.py --help)")
        source = build_source(session.kernel, feed="live" if live else "synth",
                              symbol=symbol,
                              exchange=Exchange.DERIVATIVES if live else Exchange.CASH,
                              frame=frame, live_kwargs=live_kwargs,
                              depth_levels=0 if live else args.depth_levels,
                              depth_imbalance=args.depth_imbalance,
                              depth_imbalance_mode=args.depth_mode,
                              depth_seed=args.depth_seed)
        runner = LiveRunner(session.kernel, source, poll_interval=args.poll,
                            sync_interval=args.sync)
        runner.run(duration=args.duration)

        from ntrade.events.order import OrderFilledEvent
        from ntrade.events.risk import SignalGeneratedEvent, SignalRejectedEvent
        fills = [e for e in session.kernel.bus.history
                 if isinstance(e, OrderFilledEvent)]
        signals = [e for e in session.kernel.bus.history
                   if isinstance(e, SignalGeneratedEvent)]
        rejects = [e for e in session.kernel.bus.history
                   if isinstance(e, SignalRejectedEvent)]
        print("\n== valentini live run summary ==")
        print(f"  ticks published : {source.ticks_published}")
        print(f"  polls / syncs   : {runner.polls} / {runner.syncs}")
        print(f"  signals         : {len(signals)}  rejected: {len(rejects)}")
        for s in signals:
            print(f"    {s.side} x{s.quantity} @~{s.price or 'mkt'} "
                  f"meta={ {k: v for k, v in s.metadata.items() if k in ('sl', 'tp', 'rr', 'exit_reason')} }")
        if rejects:
            print("  rejections      :")
            for r in rejects[:20]:
                print(f"    {r.side} x{r.signal.quantity} {r.signal.symbol}: {r.reason}")
            if len(rejects) > 20:
                print(f"    ... and {len(rejects) - 20} more")
        print(f"  fills           : {len(fills)}")
        for f in fills:
            print(f"    {f.side} {f.symbol} x{f.quantity} @ {f.fill_price:.2f}")
        print(f"  kill-switched   : {runner.kill_switched}  (halted: {runner.halted})")
        # Live: the broker's balance/positions are authoritative funds; paper/
        # replay: PaperBroker's own balance/positions are static, so read the
        # kernel read-models (account balance + portfolio positions) where
        # fills actually post.
        if live:
            positions = session.positions()
            print(f"  positions       : {[(p.symbol, p.quantity) for p in positions]}")
            print(f"  balance         : {session.balance():,.2f}")
        else:
            account = session.kernel.ctx.account
            positions = session.kernel.ctx.portfolio.positions
            print(f"  positions       : {[(p.symbol, p.quantity) for p in positions]}")
            print(f"  balance         : {account.balance:,.2f}")
        print("  mode            : OFFLINE CSV (no broker, no real orders)" if offline
              else ("  mode            : SYNTH (paper fills, no real orders)" if not live
                     else "  mode            : LIVE (real orders possible)"))
    finally:
        # Only a live run holds a real broker; paper/offline sessions are
        # PaperBroker (disconnect is a no-op, kept for symmetry).
        if session is not None and session.broker is not None:
            if hasattr(session.broker, "stop") and live:
                session.broker.stop()
            session.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
