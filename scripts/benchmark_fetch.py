"""Benchmark ParallelHistoryFetcher.fetch() throughput vs instrument count.

Measures wall-clock time to fetch historical data for N instruments concurrently
via PaperBroker (seeded — no mocking, no external API calls). Writes results to
.benchmarks/fetch_throughput.json.

Usage:
    .venv/bin/python scripts/benchmark_fetch.py [--instruments 5 10 20 50] [--rows 200]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.brokers.paper import PaperBroker  # noqa: E402
from ntrade.data import ParallelHistoryFetcher  # noqa: E402
from ntrade.domain.instruments.cash import Equity  # noqa: E402


class _FakeClock:
    """Pin PaperBroker timestamps for deterministic benchmark data."""

    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark ParallelHistoryFetcher.fetch()")
    p.add_argument("--instruments", type=int, nargs="+", default=[1, 2, 4, 8, 16, 50],
                   help="Instrument counts to benchmark")
    p.add_argument("--rows", type=int, default=200,
                   help="History rows per instrument (PaperBroker seed size)")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / ".benchmarks" / "fetch_throughput.json"))
    args = p.parse_args()

    broker = PaperBroker(seed=7, clock=_FakeClock(datetime(2026, 8, 3, 15, 0)))
    fetcher = ParallelHistoryFetcher(broker, max_workers=4)

    results: list[dict] = []
    for n in args.instruments:
        symbols = [f"BENCH{i:04d}" for i in range(n)]
        for sym in symbols:
            broker.seed_history(sym, rows=args.rows, timeframe="5m", start_price=100.0)
        instruments = [Equity(sym, broker=broker) for sym in symbols]

        # Warm-up (first fetch triggers seed_history auto-seed for any missing)
        fetcher.fetch(instruments[:1], timeframe="5m")

        t0 = time.perf_counter()
        df = fetcher.fetch(instruments, timeframe="5m")
        wall = time.perf_counter() - t0

        per_inst = wall / n
        rows_per_sec = len(df) / wall if wall > 0 else 0
        results.append({
            "n_instruments": n,
            "rows_fetched": len(df),
            "wall_seconds": round(wall, 4),
            "per_instrument_ms": round(per_inst * 1000, 2),
            "rows_per_sec": round(rows_per_sec, 1),
        })
        print(f"  {n:3d} instruments → {wall:.3f}s ({per_inst*1000:.1f}ms/inst, {rows_per_sec:.0f} rows/s)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
