#!/usr/bin/env python
"""Backfill NIFTY/BANKNIFTY futures OHLCV into the Parquet data store.

The dhan provider write-throughs every fetched futures history into the same
store the offline ``parquet`` provider reads, but only for contracts the UI
actually visited — so a never-visited contract (e.g. NIFTY SEP FUT) shows
"No candles" offline. This script pre-populates every supported contract.

Usage::

    python scripts/backfill_futures_parquet.py                 # all supported roots, 1m
    python scripts/backfill_futures_parquet.py --roots NIFTY    # one root
    python scripts/backfill_futures_parquet.py --symbol "NIFTY SEP FUT"

Idempotent: the parquet store upsert replaces overlapping rows, so re-running
after a gap just fills the missing range.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Run from anywhere: ``api`` is a top-level package in the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

log = logging.getLogger("ntrade.scripts.backfill_futures")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backfill NIFTY/BANKNIFTY futures into ParquetStore")
    p.add_argument("--roots", nargs="*", default=None,
                   help="Roots to backfill (default: NIFTY BANKNIFTY)")
    p.add_argument("--symbol", default=None,
                   help="Single contract symbol to backfill (overrides --roots)")
    p.add_argument("--timeframe", default="1m", help="Resolution (default: 1m)")
    p.add_argument("--env", default=".env", help="Path to .env for Dhan auth (default: .env)")
    p.add_argument("--sleep", type=float, default=2.0,
                   help="Seconds between contracts (Dhan rate-limit courtesy)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")

    from api.marketdata import DhanProvider, FuturesMaster, SUPPORTED_ROOTS

    master = FuturesMaster()
    if args.symbol:
        contracts = [c for root in master.index_roots()
                     for c in master.contracts(root)
                     if c.symbol == args.symbol.upper()]
    else:
        roots = args.roots or list(SUPPORTED_ROOTS)
        contracts = [c for root in roots for c in master.contracts(root)]
    if not contracts:
        log.error("No contracts matched (symbol=%r roots=%r)", args.symbol, args.roots)
        return 1

    provider = DhanProvider(master, env_path=args.env)
    log.info("Backfilling %d contracts (%s) via dhan → parquet", len(contracts), args.timeframe)
    ok, failed = 0, []
    for c in contracts:
        try:
            rows = provider.candles(symbol=c.symbol, exchange="NFO", interval=args.timeframe)
            if rows:
                first = rows[0]["time"]
                last = rows[-1]["time"]
                log.info("%-20s %5d rows  %s → %s", c.symbol, len(rows), first, last)
                ok += 1
            else:
                log.warning("%-20s 0 rows (no history from broker)", c.symbol)
            time.sleep(args.sleep)
        except Exception as exc:
            log.exception("%s failed: %s", c.symbol, exc)
            failed.append(c.symbol)

    log.info("Done: %d/%d contracts backfilled%s", ok, len(contracts),
             f", failed: {failed}" if failed else "")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
