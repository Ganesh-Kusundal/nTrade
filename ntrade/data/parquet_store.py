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

import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ntrade.domain.market_hours import DAILY_TIMEFRAMES, session_close, session_open

# Columns stored in every parquet row group
_BASE_COLUMNS = [
    "symbol", "exchange", "kind", "timeframe", "timestamp",
    "open", "high", "low", "close", "volume",
]


def _session_bar_mask(df: pd.DataFrame) -> pd.Series:
    """True for daily bars and intraday bars inside exchange session hours."""
    tf = df["timeframe"].astype(str).str.lower()
    daily = tf.isin(DAILY_TIMEFRAMES)
    t = df["timestamp"].dt.time
    mcx = df["exchange"].astype(str).str.upper().eq("MCX")
    nse_open, nse_close = session_open("NSE"), session_close("NSE")
    mcx_open, mcx_close = session_open("MCX"), session_close("MCX")
    in_nse = (t >= nse_open) & (t < nse_close)
    in_mcx = (t >= mcx_open) & (t < mcx_close)
    return daily | (mcx & in_mcx) | (~mcx & in_nse)


class ParquetStorage:
    """Hive-partitioned Parquet store for OHLCV history.

    ``base_path`` is the root directory; the store creates
    ``base_path/ohlcv/symbol=.../year=.../month=.../`` as needed.
    """

    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path)
        # ponytail: if caller passes 'data/ohlcv' (the actual store dir),
        # don't double-nest to data/ohlcv/ohlcv. If base_path already ends
        # with 'ohlcv', use it directly as the root.
        if self.base_path.name == "ohlcv":
            self._ohlcv_root = self.base_path
        else:
            self._ohlcv_root = self.base_path / "ohlcv"
        self._ohlcv_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ write

    def upsert(self, df: pd.DataFrame) -> int:
        """Insert-or-replace rows by (symbol, timeframe, timestamp).

        Deletes overlapping rows first (idempotent re-fetch), then appends
        new data. Returns the number of rows written (post-dedup).
        """
        if df is None or df.empty:
            return 0

        df = self._prepare_frame(df)
        written = 0

        # Group by partition and write each partition atomically
        for symbol in df["symbol"].unique():
            sub = df[df["symbol"] == symbol]
            for (year, month), grp in sub.groupby([sub["timestamp"].dt.year, sub["timestamp"].dt.month]):
                # ponytail: year/month from the timestamp group
                partition_dir = self._ohlcv_root / f"symbol={symbol}" / f"year={year}" / f"month={int(month):02d}"
                partition_dir.mkdir(parents=True, exist_ok=True)
                parquet_file = partition_dir / "data.parquet"

                # Delete overlapping rows (same symbol+timeframe+timestamp)
                if parquet_file.exists():
                    existing = pq.ParquetFile(parquet_file).read().to_pandas()
                    existing["timestamp"] = pd.to_datetime(existing["timestamp"])
                    # ponytail: normalize tz-aware stored timestamps to
                    # naive so overlap detection matches tz-naive incoming data
                    if getattr(existing["timestamp"].dt, "tz", None) is not None:
                        existing["timestamp"] = existing["timestamp"].dt.tz_localize(None)
                    # Boolean row mask: True = existing row NOT in the incoming set (to keep)
                    key_cols = ["symbol", "timeframe", "timestamp"]
                    existing = existing[~existing.set_index(key_cols).index.isin(
                        grp.set_index(key_cols).index
                    )]
                    if not existing.empty:
                        # Rewrite the partition file without the overlapping rows
                        self._write_parquet(existing, parquet_file)
                    else:
                        parquet_file.unlink()
                    # Then append new data
                    to_write = grp
                else:
                    to_write = grp

                if not to_write.empty:
                    self._write_parquet(to_write, parquet_file)
                    written += len(to_write)

        return written

    def _write_parquet(self, df: pd.DataFrame, path: Path) -> None:
        """Write a DataFrame to parquet (append if file exists)."""
        if path.exists():
            # Read existing + new, dedupe, rewrite
            existing = pq.ParquetFile(path).read().to_pandas()
            existing["timestamp"] = pd.to_datetime(existing["timestamp"])
            # ponytail: normalize tz to match incoming (fix dedup mismatch)
            if getattr(existing["timestamp"].dt, "tz", None) is not None:
                existing["timestamp"] = existing["timestamp"].dt.tz_localize(None)
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["symbol", "timeframe", "timestamp"], keep="last")
            table = pa.Table.from_pandas(combined, preserve_index=False)
            # ponytail: use_dictionary=False prevents dictionary-vs-large_string
            # type mismatch when appending frames with different column sets
            pq.write_table(table, str(path), use_dictionary=False)
        else:
            table = pa.Table.from_pandas(df, preserve_index=False)
            pq.write_table(table, str(path), use_dictionary=False)

    def _prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure required columns + types for parquet storage."""
        df = df.copy()  # ponytail: never mutate caller's DataFrame
        for col in _BASE_COLUMNS:
            if col not in df.columns:
                df[col] = None
        df = df[_BASE_COLUMNS + [
            c for c in df.columns if c not in _BASE_COLUMNS
        ]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        # ponytail: broker data ships tz-aware (e.g. Dhan UTC+5:30). Storage
        # contract is tz-naive IST wall time — strip to keep read()/duckdb_scan
        # comparisons consistent (fix: Cannot compare tz-naive vs tz-aware).
        if getattr(df["timestamp"].dt, "tz", None) is not None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)
        # Drop after-hours intraday bars at the trust boundary. Existing
        # partitions stay dirty until rewritten; readers that care (ORB
        # screener) still clip in SQL.
        df = df.loc[_session_bar_mask(df)].copy()
        # Ensure numeric types
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = df["volume"].fillna(0).astype("Int64")
        return df

    # ------------------------------------------------------------------ read

    def read(self, symbols: list[str] | None = None,
             start: datetime | str | None = None,
             end: datetime | str | None = None,
             timeframe: str | None = None) -> pd.DataFrame:
        """Read OHLCV data with partition pruning.

        Push-down predicates:
          - symbol (Hive partition)
          - year/month (Hive partition)
          - timestamp (parquet row-group statistics)
          - timeframe (optional filter)
        """
        if symbols is not None and len(symbols) == 0:
            return pd.DataFrame(columns=_BASE_COLUMNS)

        start_ts = pd.Timestamp(start) if start is not None else None
        end_ts = pd.Timestamp(end) if end is not None else None
        # Stored timestamps are tz-naive; period_range rejects tz-aware
        # endpoints, so normalize any tz-aware caller input here.
        if start_ts is not None and start_ts.tzinfo is not None:
            start_ts = start_ts.tz_localize(None)
        if end_ts is not None and end_ts.tzinfo is not None:
            end_ts = end_ts.tz_localize(None)

        # Build Hive partition path filter for year/month
        hive_paths: list[Path] = []
        if start_ts is not None and end_ts is not None:
            for sym in (symbols or self._all_symbols()):
                # Only scan months that overlap [start, end]. period_range
                # (unlike Timestamp.floor/ceil on "MS") is a non-fixed
                # frequency, which pandas 3.0 refuses to round — and it
                # includes both boundary months naturally.
                for period in pd.period_range(start=start_ts, end=end_ts, freq="M"):
                    pdir = self._ohlcv_root / f"symbol={sym}" / f"year={period.year}" / f"month={period.month:02d}"
                    if pdir.exists():
                        hive_paths.append(pdir)
        elif symbols is not None:
            for sym in symbols:
                sym_dir = self._ohlcv_root / f"symbol={sym}"
                if sym_dir.exists():
                    hive_paths.extend(p for p in sym_dir.rglob("month=*") if p.is_dir())
        else:
            hive_paths = list(self._ohlcv_root.rglob("month=*"))

        if not hive_paths:
            return pd.DataFrame(columns=_BASE_COLUMNS)

        tables = []
        for pdir in hive_paths:
            parquet_file = pdir / "data.parquet"
            if not parquet_file.exists():
                continue
            # ponytail: ParquetFile.read() avoids Dataset API schema-merge bug
            # with pandas 3.0 string metadata (pq.read_table fails on mixed
            # dictionary/large_string encodings). Filtering by timeframe is
            # done in pandas below instead of arrow filters= pushdown.
            table = pq.ParquetFile(parquet_file).read()
            if table.num_rows > 0:
                tables.append(table.to_pandas())

        if not tables:
            return pd.DataFrame(columns=_BASE_COLUMNS)

        result = pd.concat(tables, ignore_index=True)
        result["timestamp"] = pd.to_datetime(result["timestamp"])
        # ponytail: normalize tz-aware stored timestamps to naive for
        # consistent comparison with tz-naive start/end params
        if getattr(result["timestamp"].dt, "tz", None) is not None:
            result["timestamp"] = result["timestamp"].dt.tz_localize(None)
        if start_ts is not None:
            result = result[result["timestamp"] >= start_ts]
        if end_ts is not None:
            result = result[result["timestamp"] <= end_ts]
        if timeframe is not None:
            result = result[result["timeframe"] == timeframe]
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
                ts_val = pd.Timestamp(start)
                if ts_val.tzinfo is not None:
                    ts_val = ts_val.tz_localize(None)
                parts.append(f"{ts} >= TIMESTAMP '{ts_val.strftime('%Y-%m-%d %H:%M:%S')}'")
            if end is not None:
                ts_val = pd.Timestamp(end)
                if ts_val.tzinfo is not None:
                    ts_val = ts_val.tz_localize(None)
                parts.append(f"{ts} <= TIMESTAMP '{ts_val.strftime('%Y-%m-%d %H:%M:%S')}'")
            where = " WHERE " + " AND ".join(parts)
        con.execute(
            f"CREATE OR REPLACE VIEW ohlcv AS "
            f"SELECT *, CAST(timestamp AS TIMESTAMP) AS ts "
            f"FROM parquet_scan('{path_pattern}', hive_partitioning=True){where}"
        )
        return "ohlcv"
