"""Paper->live gate: run a strategy over real historical data in synth/paper
mode and print the validation checklist that must pass before going live.

Usage: .venv/bin/python scripts/paper_gate_run.py --symbol NIFTY --days 15
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.domain.instruments.cash import Index  # noqa: E402
from ntrade.engines.strategies import EmaCrossStrategy  # noqa: E402
from ntrade.kernel.clock import ReplayClock  # noqa: E402
from ntrade.kernel.session import TradingKernel  # noqa: E402
from ntrade.runner.gate import build_paper_report  # noqa: E402
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--days", type=int, default=15)
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--initial-cash", type=float, default=100_000.0)
    args = p.parse_args()

    from ntrade.kernel.trading_session import TradingSession
    session = TradingSession.connect("dhan")
    instrument = session.index(args.symbol)
    frame = instrument.history(args.timeframe, days=args.days, force=True).df
    print(f"paper gate: {len(frame)} x {args.timeframe} bars for {args.symbol} ({args.days}d)")

    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe=args.timeframe,
                      initial_cash=args.initial_cash)
    k.register(Index(args.symbol))
    k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol=args.symbol))
    k.start()
    src = SyntheticMarketFeedSource(k, symbol=args.symbol, exchange="NSE", data=frame)
    src.start()
    src.join(timeout=120)
    k.candle_engine.flush()
    k.stop(reason="paper gate complete")

    report = build_paper_report(k, initial_cash=args.initial_cash)
    print(json.dumps(report, indent=2))
    # Fail closed: a gate that only prints a checklist does not prevent a bad
    # go-live. Exit non-zero when the report shows no fills or an unhealthy
    # drawdown, so CI / humans actually notice.
    fills = int(report.get("fills", 0) or 0)
    max_dd = float(report.get("max_drawdown_pct", 0.0) or 0.0)
    if fills == 0:
        print("PAPER GATE FAIL: 0 fills produced — strategy did not trade")
        return 1
    if max_dd > 30.0:
        print(f"PAPER GATE FAIL: max drawdown {max_dd:.1f}% exceeds 30% safety cap")
        return 1
    print("PAPER GATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
