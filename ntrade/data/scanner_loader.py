"""ScannerLoader — partition-pruned 2-3 month reads for scanner workloads.

Wraps :class:`ntrade.data.parquet_store.ParquetStorage` so a scanner can pull
the trailing N days per symbol in one in-memory frame. The heavy lifting
(partition pruning by symbol + year/month, timestamp filtering) happens in
``ParquetStorage.read``; this class is a thin, scanner-shaped API on top.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from ntrade.data.parquet_store import ParquetStorage


class ScannerLoader:
    """Partition-pruned 2-3 month reads for scanner workloads.

    Usage::

        loader = ScannerLoader(store)
        df = loader.load_universe(store.symbols()[:200], days=90)
    """

    def __init__(self, store: ParquetStorage):
        self._store = store

    def load_universe(self,
                      symbols: list[str] | None = None,
                      days: int = 90,
                      timeframe: str = "5m",
                      end: datetime | None = None) -> pd.DataFrame:
        """Load the trailing ``days`` per symbol in one in-memory frame.

        Args:
            symbols: Symbols to load; ``None`` (default) → all symbols in
                the store.
            days: Trailing window length in calendar days (default 90 —
                the 2-3 month scanner window).
            timeframe: Bar timeframe to load (e.g. ``"5m"``).
            end: Window end (inclusive); ``None`` (default) → now.

        Returns:
            A DataFrame with columns ``symbol, exchange, kind, timeframe,
            timestamp, open, high, low, close, volume`` and — for
            derivatives — ``strike, option_type, expiry``. Symbols with no
            data in the window are skipped silently (no error).
        """
        end_ts = pd.Timestamp(end) if end is not None else pd.Timestamp.now()
        # Store timestamps are tz-naive; a tz-aware ``end`` would blow up the
        # naive-vs-aware comparison in ParquetStorage.read (pandas 3.0).
        if end_ts.tzinfo is not None:
            end_ts = end_ts.tz_localize(None)
        start = end_ts - timedelta(days=days)
        return self._store.read(
            symbols=symbols,
            start=start,
            end=end_ts,
            timeframe=timeframe,
        )
