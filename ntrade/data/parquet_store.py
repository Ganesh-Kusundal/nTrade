"""ParquetStorage — Hive-partitioned Parquet store with upsert semantics.

Layout: ``base_path/ohlcv/symbol={SYMBOL}/year={YYYY}/month={MM}/data.parquet``

This keeps partition pruning efficient for both access patterns:
  - Scanner reads: ``read(symbols, start, end)`` prunes partitions by symbol
    + year/month, loading only the 2-3 months needed per symbol.
  - Backfill: ``upsert(df)`` replaces any overlapping rows (idempotent
    re-fetch) and appends new data.

duckdb + pyarrow are required — both are already installed (see pyproject.toml).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ntrade.domain.market_hours import session_close as _mh_session_close, session_open as _mh_session_open
from ntrade.domain.types import strip_tz

# Columns stored in every parquet row group
_BASE_COLUMNS = [
    "symbol", "exchange", "kind", "timeframe", "timestamp",
    "open", "high", "low", "close", "volume",
]

_DAILY_TIMEFRAMES = frozenset({"1d", "d", "day"})
# Local sentinels for the hot path (avoid per-row call) — initialized from market_hours.
_NSE_OPEN = _mh_session_open("NSE")
_NSE_CLOSE = _mh_session_close("NSE")
_MCX_OPEN = _mh_session_open("MCX")
_MCX_CLOSE = _mh_session_close("MCX")


def _session_open(exchange: str) -> time:
    return _MCX_OPEN if str(exchange).upper() == "MCX" else _NSE_OPEN


def _session_close(exchange: str) -> time:
    return _MCX_CLOSE if str(exchange).upper() == "MCX" else _NSE_CLOSE


def _session_bar_mask(df: pd.DataFrame, session_filter=None) -> pd.Series:
    """True for daily bars and intraday bars inside exchange session hours."""
    if session_filter is not None:
        return session_filter(df)
    tf = df["timeframe"].astype(str).str.lower()
    daily = tf.isin(_DAILY_TIMEFRAMES)
    t = df["timestamp"].dt.time
    mcx = df["exchange"].astype(str).str.upper().eq("MCX")
    nse_open, nse_close = _session_open("NSE"), _session_close("NSE")
    mcx_open, mcx_close = _session_open("MCX"), _session_close("MCX")
    in_nse = (t >= nse_open) & (t < nse_close)
    in_mcx = (t >= mcx_open) & (t < mcx_close)
    return daily | (mcx & in_mcx) | (~mcx & in_nse)


class ParquetStorage:
    """Hive-partitioned Parquet store for OHLCV history.

    ``base_path`` is the root directory; the store creates
    ``base_path/ohlcv/symbol=.../year=.../month=.../`` as needed.
    """

    def __init__(self, base_path: str | Path, session_filter=None):
        self.base_path = Path(base_path)
        # ponytail: if caller passes 'data/ohlcv' (the actual store dir),
        # don't double-nest to data/ohlcv/ohlcv. If base_path already ends
        # with 'ohlcv', use it directly as the root.
        if self.base_path.name == "ohlcv":
            self._ohlcv_root = self.base_path
        else:
            self._ohlcv_root = self.base_path / "ohlcv"
        self._ohlcv_root.mkdir(parents=True, exist_ok=True)
        self._session_filter = session_filter

    # ------------------------------------------------------------------ write

    def upsert(self, df: pd.DataFrame, session_filter=None) -> int:
        """Insert-or-replace rows by (symbol, timeframe, timestamp).

        Deletes overlapping rows first (idempotent re-fetch), then appends
        new data. Returns the number of rows written (post-dedup).
        """
        if df is None or df.empty:
            return 0

        effective_filter = session_filter if session_filter is not None else self._session_filter
        df = self._prepare_frame(df, session_filter=effective_filter)
        written = 0

        # Group by partition and write each partition atomically
        for symbol in df["symbol"].unique():
            sub = df[df["symbol"] == symbol]
            for (year, month), grp in sub.groupby([sub["timestamp"].dt.year, sub["timestamp"].dt.month]):
                # ponytail: year/month from the timestamp group
                partition_dir = self._ohlcv_root / f"symbol={symbol}" / f"year={year}" / f"month={int(month):02d}"
                partition_dir.mkdir(parents=True, exist_ok=True)
                parquet_file = partition_dir / "data.parquet"

                # Delete overlapping rows (same symbol+timeframe+timestamp),
                # then append new data — ONE atomic replace for the whole
                # partition update. Never unlink-then-write or rewrite-then-
                # append: a crash mid-sequence must leave the previous
                # partition intact, not lose it (or silently drop the
                # overlapping rows).
                key_cols = ["symbol", "timeframe", "timestamp"]
                if parquet_file.exists():
                    existing = pq.ParquetFile(parquet_file).read().to_pandas()
                    existing["timestamp"] = pd.to_datetime(existing["timestamp"])
                    # Storage contract is tz-naive IST wall time — strip
                    # any tz-aware stored timestamps so overlap detection
                    # matches tz-naive incoming data.
                    existing["timestamp"] = strip_tz(existing["timestamp"])
                    # Boolean row mask: True = existing row NOT in the incoming set (to keep)
                    existing = existing[~existing.set_index(key_cols).index.isin(
                        grp.set_index(key_cols).index
                    )]
                    to_write = (pd.concat([existing, grp], ignore_index=True)
                                if not existing.empty else grp)
                else:
                    to_write = grp

                if not to_write.empty:
                    self._write_parquet(to_write, parquet_file)
                    written += len(grp)

        return written

    def _write_parquet(self, df: pd.DataFrame, path: Path) -> None:
        """Write a DataFrame to parquet (append if file exists).

        Atomic: write to a tmp sibling then os.replace — a crash mid-write
        leaves the previous partition intact instead of truncating history
        (same pattern as the token-state guard in execution/_guard.py).
        """
        if path.exists():
            # Read existing + new, dedupe, rewrite
            existing = pq.ParquetFile(path).read().to_pandas()
            existing["timestamp"] = pd.to_datetime(existing["timestamp"])
            # Storage contract is tz-naive IST wall time — strip any
            # tz so dedup comparisons match the incoming frame.
            existing["timestamp"] = strip_tz(existing["timestamp"])
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["symbol", "timeframe", "timestamp"], keep="last")
            table = pa.Table.from_pandas(combined, preserve_index=False)
        else:
            table = pa.Table.from_pandas(df, preserve_index=False)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            # ponytail: use_dictionary=False prevents dictionary-vs-large_string
            # type mismatch when appending frames with different column sets
            pq.write_table(table, str(tmp), use_dictionary=False)
            os.replace(str(tmp), str(path))
        except BaseException:
            tmp.unlink(missing_ok=True)  # never leave .tmp litter on failure
            raise

    def _prepare_frame(self, df: pd.DataFrame, session_filter=None) -> pd.DataFrame:
        """Ensure required columns + types for parquet storage."""
        df = df.copy()  # ponytail: never mutate caller's DataFrame
        for col in _BASE_COLUMNS:
            if col not in df.columns:
                df[col] = None
        df = df[_BASE_COLUMNS + [
            c for c in df.columns if c not in _BASE_COLUMNS
        ]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        # Storage contract is tz-naive IST wall time. Broker data ships
        # tz-aware (e.g. Dhan UTC+5:30) — strip at the trust boundary so
        # read()/duckdb_scan comparisons stay consistent.
        df["timestamp"] = strip_tz(df["timestamp"])
        # Drop after-hours intraday bars at the trust boundary. Existing
        # partitions stay dirty until rewritten; readers that care (ORB
        # screener) still clip in SQL.
        effective = session_filter if session_filter is not None else self._session_filter
        df = df.loc[_session_bar_mask(df, session_filter=effective)].copy()
        # Ensure numeric types
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = df["volume"].fillna(0).astype("Int64")
        return df

    # ------------------------------------------------------------------ read

    def read(self, symbols: list[str] | None = None,
             start: datetime | str | None = None,
             end: datetime | str | None = None,
             timeframe: str | None = None,
             batch_size: int = 100,
             universe: list[str] | None = None,
             session_filter=None) -> pd.DataFrame:
        """Read OHLCV data with partition pruning and paginated universe.

        Push-down predicates:
          - symbol (Hive partition)
          - year/month (Hive partition)
          - timestamp (parquet row-group statistics)
          - timeframe (optional filter)

        Pagination (OOM fix): ``universe`` / ``symbols`` is batched in chunks
        of ``batch_size``; each batch builds its own Hive partition list and
        is read via pyarrow, then concatenated. For small universes the cost
        is identical to the non-batched path (single batch).
        ``universe`` is an alias for ``symbols`` to match the history
        pipeline naming.
        """
        # Alias: universe == symbols
        if universe is not None and symbols is None:
            symbols = universe
        elif universe is not None and symbols is not None:
            # both supplied — merge without duplicates preserving order
            merged = list(dict.fromkeys(list(symbols) + list(universe)))
            symbols = merged

        if symbols is not None and len(symbols) == 0:
            return pd.DataFrame(columns=_BASE_COLUMNS)

        if batch_size is None or batch_size <= 0:
            batch_size = 100

        start_ts = pd.Timestamp(start) if start is not None else None
        end_ts = pd.Timestamp(end) if end is not None else None
        # Stored timestamps are tz-naive; period_range rejects tz-aware
        # endpoints, so normalize any tz-aware caller input here.
        start_ts = strip_tz(start_ts)
        end_ts = strip_tz(end_ts)

        # Resolve effective symbol list for batching
        if symbols is not None:
            effective_symbols = list(symbols)
        else:
            effective_symbols = self._all_symbols()
            if not effective_symbols:
                return pd.DataFrame(columns=_BASE_COLUMNS)

        # Chunk symbols to avoid loading all partitions at once (OOM on wide universe)
        batches: list[list[str]] = [
            effective_symbols[i:i + batch_size]
            for i in range(0, len(effective_symbols), batch_size)
        ]

        all_frames: list[pd.DataFrame] = []
        for batch in batches:
            # Build Hive partition path filter for this batch
            hive_paths: list[Path] = []
            if start_ts is not None and end_ts is not None:
                for sym in batch:
                    for period in pd.period_range(start=start_ts, end=end_ts, freq="M"):
                        pdir = self._ohlcv_root / f"symbol={sym}" / f"year={period.year}" / f"month={period.month:02d}"
                        if pdir.exists():
                            hive_paths.append(pdir)
            else:
                for sym in batch:
                    sym_dir = self._ohlcv_root / f"symbol={sym}"
                    if sym_dir.exists():
                        hive_paths.extend(p for p in sym_dir.rglob("month=*") if p.is_dir())

            if not hive_paths:
                continue

            tables = []
            for pdir in hive_paths:
                parquet_file = pdir / "data.parquet"
                if not parquet_file.exists():
                    continue
                table = pq.ParquetFile(parquet_file).read()
                if table.num_rows > 0:
                    tables.append(table.to_pandas())

            if not tables:
                continue

            chunk_df = pd.concat(tables, ignore_index=True)
            chunk_df["timestamp"] = pd.to_datetime(chunk_df["timestamp"])
            chunk_df["timestamp"] = strip_tz(chunk_df["timestamp"])
            if start_ts is not None:
                chunk_df = chunk_df[chunk_df["timestamp"] >= start_ts]
            if end_ts is not None:
                chunk_df = chunk_df[chunk_df["timestamp"] <= end_ts]
            if timeframe is not None:
                chunk_df = chunk_df[chunk_df["timeframe"] == timeframe]
            if not chunk_df.empty:
                all_frames.append(chunk_df)

        if not all_frames:
            return pd.DataFrame(columns=_BASE_COLUMNS)

        result = pd.concat(all_frames, ignore_index=True)
        result["timestamp"] = pd.to_datetime(result["timestamp"])
        result["timestamp"] = strip_tz(result["timestamp"])
        return result.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    # ------------------------------------------------------------------ metadata

    def symbols(self) -> list[str]:
        """All symbols present in the store."""
        return self._all_symbols()

    def _all_symbols(self) -> list[str]:
        if not self._ohlcv_root.exists():
            return []
        return sorted(
            p.name.split("=", 1)[1]
            for p in self._ohlcv_root.iterdir()
            if p.is_dir() and p.name.startswith("symbol=")
        )

    def date_range(self, symbol: str, timeframe: str | None = None) -> tuple[datetime, datetime] | None:
        """Min/max timestamp for a symbol (optionally filtered by timeframe)."""
        df = self.read(symbols=[symbol], timeframe=timeframe)
        if df.empty:
            return None
        return df["timestamp"].min(), df["timestamp"].max()

    def clear(self) -> None:
        """Wipe all stored data (test isolation)."""
        if self._ohlcv_root.exists():
            shutil.rmtree(self._ohlcv_root)
        self._ohlcv_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------- DuckDB integration (optional)

    def duckdb_scan(self, con, *, start: datetime | str | None = None,
                    end: datetime | str | None = None) -> str:
        """Register the parquet root as a DuckDB view for SQL queries.

        Defaults to the trailing 1-minute window (``start=now-1min, end=now``)
        for live SQL querying without explicit bounds. Pass ``start``/``end``
        to query an arbitrary range.

        Usage::
            import duckdb
            store = ParquetStorage("/data/ohlcv")
            con = duckdb.connect()
            store.duckdb_scan(con)
            con.execute("SELECT * FROM ohlcv WHERE symbol='NIFTY'").fetchdf()
        """
        # ponytail: DuckDB parquet_scan reads Hive partitions natively
        path_pattern = str(self._ohlcv_root / "**" / "data.parquet")
        if start is None and end is None:
            now = pd.Timestamp.now()
            start = now - pd.Timedelta(minutes=1)
            end = now
        where = ""
        if start is not None or end is not None:
            ts = "timestamp"
            parts = []
            if start is not None:
                ts_val = strip_tz(pd.Timestamp(start))
                parts.append(f"{ts} >= TIMESTAMP '{ts_val.strftime('%Y-%m-%d %H:%M:%S')}'")
            if end is not None:
                ts_val = strip_tz(pd.Timestamp(end))
                parts.append(f"{ts} <= TIMESTAMP '{ts_val.strftime('%Y-%m-%d %H:%M:%S')}'")
            where = " WHERE " + " AND ".join(parts)
        con.execute(
            f"CREATE OR REPLACE VIEW ohlcv AS "
            f"SELECT *, CAST(timestamp AS TIMESTAMP) AS ts "
            f"FROM parquet_scan('{path_pattern}', hive_partitioning=True){where}"
        )
        return "ohlcv"
