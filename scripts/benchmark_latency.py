"""Kernel event-pipeline latency benchmark; writes .benchmarks/latency.json.

Usage: .venv/bin/python scripts/benchmark_latency.py [--ticks 10000]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.kernel.clock import ReplayClock  # noqa: E402
from ntrade.kernel.session import TradingKernel  # noqa: E402
from ntrade.runner.bench import measure_tick_throughput  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticks", type=int, default=10_000)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / ".benchmarks" / "latency.json"))
    args = p.parse_args()

    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    stats = measure_tick_throughput(k, n_ticks=args.ticks)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
