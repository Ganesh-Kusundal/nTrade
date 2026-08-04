"""Data layer — parallel historical fetching, Parquet storage, and scanner loading.

Public surface:
    - ``ParallelHistoryFetcher`` — concurrent multi-instrument history fetch
    - ``ParquetStorage``         — Hive-partitioned (symbol/time) Parquet store
    - ``GapDetector``            — diff requested universe vs stored data
    - ``ScannerLoader``         — efficient 2-3-month-per-symbol reads for scanners
    - ``load_universe``         — load Nifty constituent CSV → Equity instruments
"""

from ntrade.data.history_pipeline import ParallelHistoryFetcher
from ntrade.data.parquet_store import ParquetStorage
from ntrade.data.gap_detector import GapDetector
from ntrade.data.scanner_loader import ScannerLoader
from ntrade.data.universe import load_universe, available_universes

__all__ = [
    "ParallelHistoryFetcher",
    "ParquetStorage",
    "GapDetector",
    "ScannerLoader",
    "load_universe",
    "available_universes",
]
