#!/usr/bin/env python
"""Backfill OHLCV history into the Parquet data store.

Usage (live, real data)::

    python scripts/backfill_parquet.py \
        --universe nifty500 \
        --timeframe 1m \
        --months 3 \
        --broker dhan

Usage (dry-run against PaperBroker, which seeds synthetic history)::

    python scripts/backfill_parquet.py \
        --universe nifty500 \
        --timeframe 1m \
        --months 3 \
        --broker paper \
        --dry-run \
        --limit 5

Design
------
The ``ParallelHistoryFetcher.fetch()`` returns a *single merged* DataFrame for
all instruments.  For 1-minute data across 500 stocks that would be ~35 M rows
— an OOM hazard.  Instead we process instruments in **small batches**: fetch a
batch concurrently, upsert immediately, then discard the DataFrame so memory
stays bounded regardless of universe size.

Each batch upsert is **idempotent**: re-running the script on an already-backed-
filled partition silently replaces overlapping rows (B-017 fix in parquet_store)
and skips gaps via ``GapDetector``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ntrade.data import ParquetStorage, ParallelHistoryFetcher, load_universe
from ntrade.data.gap_detector import GapDetector

# --------------------------------------------------------------------------- #
# Brokers
# --------------------------------------------------------------------------- #
from ntrade.brokers.paper import PaperBroker

log = logging.getLogger("ntrade.scripts.backfill")


def _build_broker(name: str, env_path: str = ".env"):
    """Return a broker adapter for the given name."""
    if name == "paper":
        broker = PaperBroker()
        # seed_history is called per-symbol in the dry-run path
        return broker
    if name == "dhan":
        from ntrade.brokers.dhan import DhanBroker
        return DhanBroker(env_path=env_path)
    raise ValueError(f"unknown broker: {name!r}")


def _date_args(months: int) -> tuple[datetime, datetime]:
    """Return (start, end) covering the trailing ``months`` months."""
    end = datetime.now()
    start = end - pd.DateOffset(months=months)
    return start.to_pydatetime(), end


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backfill OHLCV into ParquetStore")
    p.add_argument("--universe", default="nifty500",
                   choices=["nifty50", "nifty100", "nifty200", "nifty500"],
                   help="Nifty universe CSV to load (default: nifty500)")
    p.add_argument("--timeframe", default="1m",
                   help="Resolution to backfill (default: 1m)")
    p.add_argument("--months", type=int, default=3,
                   help="How many trailing months to backfill (default: 3)")
    p.add_argument("--broker", default="dhan",
                   choices=["dhan", "paper"],
                   help="Broker adapter (default: dhan)")
    p.add_argument("--env", default=".env",
                   help="Path to .env for Dhan auth (default: .env)")
    p.add_argument("--data-root", default=None,
                   help="Parquet base path (default: data/ — store lives at data/ohlcv/)")
    p.add_argument("--out-dir", default="data/ohlcv_synthetic",
                   help="Synthetic data output dir (default: data/ohlcv_synthetic)")
    p.add_argument("--batch-size", type=int, default=20,
                   help="Symbols to fetch per batch (default: 20)")
    p.add_argument("--workers", type=int, default=4,
                   help="Concurrent fetchers per batch (default: 4)")
    p.add_argument("--dry-run", action="store_true",
                   help="Use PaperBroker with seeded synthetic data — no real API calls")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap number of symbols processed (0 = no limit)")
    p.add_argument("--skip-existing", action="store_true",
                   help="Skip symbols that already have full coverage (gap-aware)")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    # ----- resolve dates --------------------------------------------------- #
    start, end = _date_args(args.months)
    log.info("Backfill window: %s → %s (%d months, %s timeframe)",
             start, end, args.months, args.timeframe)

    # ----- load universe --------------------------------------------------- #
    instruments = load_universe(args.universe)
    if not instruments:
        log.error("No instruments loaded for universe %r", args.universe)
        return 1
    if args.limit > 0:
        instruments = instruments[:args.limit]
    log.info("Loaded %d instruments from universe %r", len(instruments), args.universe)

    # ----- init broker + store + fetcher ----------------------------------- #
    broker = _build_broker(args.broker, env_path=args.env)
    data_root = Path(args.out_dir) if args.out_dir else Path("data")
    store = ParquetStorage(data_root)
    fetcher = ParallelHistoryFetcher(broker, max_workers=args.workers)
    gap_detector = GapDetector(store)

    # ----- dry-run seeding (PaperBroker only) ------------------------------ #
    if args.dry_run:
        if args.broker != "paper":
            log.error("--dry-run requires --broker paper")
            return 1
        log.info("Seeding PaperBroker with synthetic 1-minute history for %d symbols",
                 len(instruments))
        days = (end - start).days
        synthetic_dir = Path(args.out_dir) if args.out_dir else Path("data/ohlcv_synthetic")
        synthetic_dir.mkdir(parents=True, exist_ok=True)
        import warnings
        warnings.warn(
            "Synthetic data writes to `data/ohlcv_synthetic` — never mix with `data/ohlcv`",
            UserWarning,
        )
        for inst in instruments:
            broker.seed_history(inst.symbol, rows=days * 390, timeframe="1m", start_price=100.0)

    # ----- optional gap-aware skip ----------------------------------------- #
    to_fetch = instruments
    if args.skip_existing:
        log.info("Detecting gaps…")
        gaps = gap_detector.detect(instruments, start=start, end=end,
                                   timeframe=args.timeframe, bar_freq="1min")
        missing = set()
        for inst, ranges in gaps:
            if ranges:
                missing.add(inst.symbol)
        to_fetch = [inst for inst in instruments if inst.symbol in missing] \
            if missing else []
        log.info("Gap detection: %d symbols still need data, %d already complete",
                 len(missing), len(instruments) - len(missing))

    if not to_fetch:
        log.info("Nothing to backfill — all data already present.")
        return 0

    # ----- main loop: batch fetch → upsert → discard ----------------------- #
    total_written = 0
    failed = []
    batches = [to_fetch[i:i + args.batch_size]
               for i in range(0, len(to_fetch), args.batch_size)]

    for batch_idx, batch in enumerate(batches, 1):
        t0 = time.perf_counter()
        try:
            # fetch returns a single merged DataFrame for this batch
            df = fetcher.fetch(batch, timeframe=args.timeframe, start=start, end=end)
            if df is None or df.empty:
                log.warning("Batch %d/%d returned empty data — symbols: %s",
                            batch_idx, len(batches),
                            ", ".join(i.symbol for i in batch))
                continue

            written = store.upsert(df)
            total_written += written
            elapsed = time.perf_counter() - t0
            rate = written / elapsed if elapsed > 0 else 0
            log.info("Batch %d/%d: %d rows in %.1fs (%.0f rows/s) — %s",
                     batch_idx, len(batches), written, elapsed, rate,
                     ", ".join(i.symbol for i in batch))
        except Exception as exc:
            log.exception("Batch %d failed: %s", batch_idx, exc)
            failed.extend(i.symbol for i in batch)
            continue

    log.info("=" * 60)
    log.info("Backfill complete: %d rows written across %d batches",
             total_written, len(batches))
    if failed:
        log.warning("Failed symbols (%d): %s", len(failed),
                     ", ".join(failed[:20]))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
