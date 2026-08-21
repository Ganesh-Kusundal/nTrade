"""Partition writes must be atomic — a crash mid-write must leave the
previous partition intact, not truncate a month of history."""
import pandas as pd


def _frame(day, hours):
    return pd.DataFrame({
        "symbol": "NIFTY", "exchange": "NFO", "kind": "live", "timeframe": "1m",
        "timestamp": [pd.Timestamp(2026, 8, day, h) for h in hours],
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10,
    })


def test_upsert_never_leaves_torn_partition(tmp_path, monkeypatch):
    from ntrade.data.parquet_store import ParquetStorage

    store = ParquetStorage(str(tmp_path))
    store.upsert(_frame(20, [9, 10]))
    path = next(tmp_path.rglob("data.parquet"))
    before = pd.read_parquet(path)

    # A crash during pq.write_table: simulate by exploding on write.
    import pyarrow.parquet as pq
    real = pq.write_table

    def boom(*a, **kw):
        raise OSError("disk full mid-write")

    monkeypatch.setattr(pq, "write_table", boom)
    try:
        store.upsert(_frame(20, [10, 11]))
    except OSError:
        pass
    monkeypatch.setattr(pq, "write_table", real)

    after = pd.read_parquet(path)
    assert len(after) == len(before), "partition was torn by the failed write"


def test_upsert_overlap_never_drops_rows_on_crash(tmp_path, monkeypatch):
    """The overlap path (existing rows replaced by incoming ones) must also be
    crash-safe: the partition update is one atomic replace, never
    unlink-then-write — a crash in that window used to delete the whole
    partition."""
    from ntrade.data.parquet_store import ParquetStorage

    store = ParquetStorage(str(tmp_path))
    store.upsert(_frame(20, [10, 11]))
    path = next(tmp_path.rglob("data.parquet"))

    # Overlapping upsert (hour 11 replaced, hour 12 added) crashing mid-write.
    import pyarrow.parquet as pq
    real = pq.write_table

    def boom(*a, **kw):
        raise OSError("crash during overlap rewrite")

    monkeypatch.setattr(pq, "write_table", boom)
    try:
        store.upsert(_frame(20, [11, 12]))
    except OSError:
        pass
    monkeypatch.setattr(pq, "write_table", real)

    after = pd.read_parquet(path)
    assert sorted(after["timestamp"].dt.hour.tolist()) == [10, 11], (
        "partition must survive a crash during an overlapping upsert")

    # After the crash the upsert retries successfully — final state is
    # hours 10, 11, 12 with hour 11 replaced (same values here).
    store.upsert(_frame(20, [11, 12]))
    after = pd.read_parquet(path)
    assert sorted(after["timestamp"].dt.hour.tolist()) == [10, 11, 12]
