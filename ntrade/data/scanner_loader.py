"""ScannerLoader — partition-pruned 2-3 month reads for scanner workloads."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from ntrade.data.parquet_store import ParquetStorage


class ScannerLoader:
    """Partition-pruned 2-3 month reads for scanner workloads."""

    def __init__(self, store: ParquetStorage):
        self._store = store

    def load_universe(self, symbols: Optional[list[str]] = None,
                      days: int = 90, timeframe: str = "5m",
                      end: Optional[datetime] = None) -> pd.DataFrame:
        """Load the trailing `days` per symbol in one in-memory frame.

        - `symbols=None` → all symbols in store
        - `end=None` → datetime.now()
        - Skips symbols with no data in the window (lazy, no error)
        - Returns columns symbol, exchange, kind, timeframe, timestamp,
          open, high, low, close, volume [, strike, option_type, expiry]
        """
        if end is None:
            end = datetime.now()
        start = end - timedelta(days=days)
        return self._store.read(symbols=symbols, start=start, end=end, timeframe=timeframe)