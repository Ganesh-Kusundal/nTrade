"""Run the LiveRunner harness on a symbol.

  synth mode (default): fetch 1m historical data via Dhan and extrapolate it
      to 1-second ticks offline — full pipeline rehearsal without risk.
  live mode:            drive the real Dhan websocket (--live-kwargs JSON).

Usage:
  .venv/bin/python scripts/live_runner_run.py --symbol NIFTY --days 2 --duration 5
  .venv/bin/python scripts/live_runner_run.py --feed live \
      --live-kwargs '{"symbols": [[1, 2885]], "symbol_map": {"2885": ["NIFTY", "NSE"]}}'
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.kernel.clock import LiveClock, ReplayClock  # noqa: E402
from ntrade.kernel.session import TradingKernel  # noqa: E402
from ntrade.runner.feeds import build_source  # noqa: E402
from ntrade.runner.live_runner import LiveRunner  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run the LiveRunner harness")
    p.add_argument("--feed", choices=("synth", "live"), default="synth")
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--exchange", default="NSE")
    p.add_argument("--days", type=int, default=2)
    p.add_argument("--duration", type=float, default=None, help="run seconds")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--sync", type=float, default=30.0)
    p.add_argument("--timeframe", default="1m")
    p.add_argument("--live-kwargs", default="{}", help="JSON kwargs for the live feed")
    args = p.parse_args()

    mode = "live" if args.feed == "live" else "replay"
    clock = LiveClock() if mode == "live" else ReplayClock()
    k = TradingKernel(mode=mode, clock=clock, timeframe=args.timeframe)

    frame = None
    if args.feed == "synth":
        from ntrade.domain.instruments.cash import Index
        from ntrade.kernel.trading_session import TradingSession
        session = TradingSession.connect("dhan")
        instrument = session.index(args.symbol)
        frame = instrument.market.history()(args.timeframe, days=args.days, force=True).df
        k.register(Index(args.symbol))
        print(f"synth feed: {len(frame)} x {args.timeframe} bars for {args.symbol}")

    live_kwargs = json.loads(args.live_kwargs)
    source = build_source(k, feed=args.feed, symbol=args.symbol,
                          exchange=args.exchange, frame=frame, live_kwargs=live_kwargs)
    runner = LiveRunner(k, source, poll_interval=args.poll, sync_interval=args.sync)
    runner.run(duration=args.duration)

    print(f"\n== session summary ==")
    print(f"  ticks published : {source.ticks_published}")
    print(f"  polls           : {runner.polls}   syncs: {runner.syncs}")
    print(f"  fills           : {len([e for e in k.bus.history if e.__class__.__name__ == 'OrderFilledEvent'])}")
    print(f"  balance         : {k.ctx.account.balance:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
