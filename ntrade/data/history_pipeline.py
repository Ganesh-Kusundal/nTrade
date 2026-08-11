"""ParallelHistoryFetcher — concurrent multi-instrument history fetching.

Fetches OHLCV data for many instruments concurrently via ThreadPoolExecutor.
Each worker calls ``broker.get_historical()`` which routes through
``DhanTransport._invoke(Quota.DATA)`` — the shared ``BrokerRateGate``
serializes all calls to 5/s, so workers never exceed the quota.

Worker count is tuned to the DATA quota window: 5 fetches/s means ~4 workers
leaves one slot free for the gate's own bookkeeping.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import pandas as pd

logger = logging.getLogger("ntrade.data.fetcher")

# Dhan DATA quota = 5/s. Cap workers so we don't starve the gate's overhead.
# ponytail: derived from BrokerRateGate.DEFAULT_WINDOWS[Quota.DATA] = (1.0, 5)
_MAX_WORKERS = 4


class ParallelHistoryFetcher:
    """Fetch historical data for multiple instruments in parallel.

    Usage::

        fetcher = ParallelHistoryFetcher(broker)
        df = fetcher.fetch(instruments, timeframe="5m", days=90)
    """

    def __init__(self, broker, max_workers: int = _MAX_WORKERS):
        self.broker = broker
        self.max_workers = max_workers

    def fetch(self,
              instruments: Iterable,
              timeframe: str = "5m",
              days: int | None = None,
              start=None,
              end=None,
              max_workers: int | None = None) -> pd.DataFrame:
        """Fetch history for all instruments concurrently.

        Returns a merged DataFrame with one row per bar, tagged with
        ``symbol``, ``exchange``, ``kind``, ``timeframe`` and — for
        derivatives — ``strike``, ``option_type``, ``expiry``.
        """
        insts = list(instruments)
        if not insts:
            return pd.DataFrame()

        workers = max_workers or self.max_workers
        lock = threading.Lock()
        rows: list[pd.DataFrame] = []
        errors: list[str] = []

        def _fetch_one(inst) -> pd.DataFrame | None:
            try:
                cs = self.broker.get_historical(
                    inst, timeframe=timeframe, days=days, start=start, end=end,
                )
                df = cs.to_dataframe() if hasattr(cs, "to_dataframe") else cs
                if df is None or df.empty:
                    return None
                df = df.copy()
                df["symbol"] = inst.symbol
                df["exchange"] = inst.exchange
                df["kind"] = inst.KIND
                df["timeframe"] = timeframe
                # Derivatives get extra metadata columns
                if inst.KIND == "option":
                    df["strike"] = getattr(inst, "strike", None)
                    df["option_type"] = getattr(inst, "option_type", None)
                    df["expiry"] = getattr(inst, "expiry", None)
                if inst.KIND == "future":
                    df["expiry"] = getattr(inst, "expiry", None)
                return df
            except Exception as exc:
                logger.error("fetch failed for %s: %s", inst.symbol, exc)
                with lock:
                    errors.append(f"{inst.symbol}: {exc}")
                return None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_one, inst): inst.symbol for inst in insts}
            for fut in as_completed(futures):
                result = fut.result()
                if result is not None:
                    with lock:
                        rows.append(result)

        if errors:
            logger.warning("fetch completed with %d errors: %s", len(errors), errors[:3])

        if not rows:
            return pd.DataFrame()
        return pd.concat(rows, ignore_index=True)
