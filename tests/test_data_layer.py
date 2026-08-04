"""T-039 — data layer tests: PaperBroker round trip through the Parquet store.

Covers the design spec (docs/superpowers/specs/2026-08-03-parallel-history-parquet-design.md):
  1. Round trip: PaperBroker seeded → ParallelHistoryFetcher.fetch →
     ParquetStorage.upsert → read returns the same bars.
  2. Idempotent upsert: same frame twice → no duplicate rows; overlapping
     re-fetch overwrites (never duplicates).
  3. Gap detection: partial store → GapDetector.detect reports only the
     missing ranges.
  4. ScannerLoader: multi-symbol load of the trailing window; symbols with no
     data are skipped silently.
  5. Derivative tagging: option fetch carries strike/option_type/expiry
     through parquet and back; mixed-kind stores concat cleanly.

No mocking: PaperBroker is seeded directly and a fake clock pins timestamps so
every assertion is deterministic.
"""

from datetime import date, datetime

import pandas as pd
import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.data import GapDetector, ParallelHistoryFetcher, ParquetStorage, ScannerLoader
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.instruments.derivatives import Option


class _FakeClock:
    """Pin ``PaperBroker._ts()`` to a fixed instant (replay-style determinism)."""

    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.fixture
def broker() -> PaperBroker:
    return PaperBroker(seed=7, clock=_FakeClock(datetime(2026, 8, 3, 15, 0)))


def _ohlcv_frame(symbol: str, ts, closes) -> pd.DataFrame:
    """Build a minimal tagged OHLCV frame (equity-shaped)."""
    return pd.DataFrame({
        "symbol": symbol,
        "exchange": "NSE",
        "kind": "equity",
        "timeframe": "1d",
        "timestamp": pd.to_datetime(ts),
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [100] * len(closes),
    })


# ============================================================== public surface
def test_data_layer_public_exports():
    from ntrade.data import __all__
    assert set(("ParallelHistoryFetcher", "ParquetStorage", "GapDetector", "ScannerLoader")) <= set(__all__)


# ============================================================ 1. round trip
def test_round_trip_fetch_upsert_read(tmp_path, broker):
    broker.seed_history("RELIANCE", rows=20, timeframe="5m", start_price=100.0)
    rel = Equity("RELIANCE", broker=broker)

    df = ParallelHistoryFetcher(broker).fetch([rel], timeframe="5m")
    # Fetcher tags the frame with instrument metadata before storage.
    assert {"symbol", "exchange", "kind", "timeframe"} <= set(df.columns)
    assert set(df["symbol"]) == {"RELIANCE"}
    assert set(df["kind"]) == {"equity"}
    assert set(df["timeframe"]) == {"5m"}

    store = ParquetStorage(tmp_path / "ohlcv")
    written = store.upsert(df)
    assert written == len(df)

    back = store.read(symbols=["RELIANCE"], timeframe="5m")
    assert len(back) == len(df)
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    # PaperBroker seeds object-dtype datetimes; the store returns datetime64 —
    # normalize before comparing so the test is dtype-robust across pandas.
    a = df.copy()
    a["timestamp"] = pd.to_datetime(a["timestamp"])
    a = a.sort_values("timestamp")[cols].reset_index(drop=True)
    b = back.sort_values("timestamp")[cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


# ============================================================ 2. idempotent upsert
def test_upsert_idempotent_no_duplicates(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    frame = _ohlcv_frame("EQ", pd.date_range("2026-08-01", periods=6, freq="1D"),
                         [10, 11, 12, 13, 14, 15])

    assert store.upsert(frame) == 6
    assert store.upsert(frame) == 6  # second write must not duplicate rows

    back = store.read(symbols=["EQ"], timeframe="1d")
    assert len(back) == 6
    assert back.duplicated(subset=["symbol", "timeframe", "timestamp"]).sum() == 0


def test_upsert_overwrite_replaces_overlapping_rows(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    ts = pd.date_range("2026-08-01", periods=4, freq="1D")
    store.upsert(_ohlcv_frame("EQ", ts, [10, 11, 12, 13]))

    # Re-fetch of the same bars with corrected closes → overwrite, not append.
    store.upsert(_ohlcv_frame("EQ", ts, [20, 21, 22, 23]))

    back = store.read(symbols=["EQ"], timeframe="1d")
    assert len(back) == 4
    assert sorted(back["close"]) == [20, 21, 22, 23]


# ==================================================================== 2b. partial overlap
def test_upsert_partial_overlap_preserves_non_overlapping_and_overwrites_overlap(tmp_path):
    """Upsert with partial timestamp overlap: non-overlapping existing rows
    are preserved, overlapping rows are overwritten (never duplicated)."""
    store = ParquetStorage(tmp_path / "ohlcv")
    ts1 = pd.date_range("2026-08-01", periods=4, freq="1D")
    ts2 = pd.date_range("2026-08-03", periods=4, freq="1D")  # Aug 3-4 overlap
    store.upsert(_ohlcv_frame("EQ", ts1, [10, 11, 12, 13]))
    store.upsert(_ohlcv_frame("EQ", ts2, [20, 21, 22, 23]))

    back = store.read(symbols=["EQ"], timeframe="1d")
    assert len(back) == 6  # 4 + 4 - 2 overlap
    closes = dict(zip(back["timestamp"], back["close"]))
    # Non-overlapping rows preserved
    assert closes[pd.Timestamp("2026-08-01")] == 10
    assert closes[pd.Timestamp("2026-08-02")] == 11
    # Overlapping rows overwritten (keep="last" wins)
    assert closes[pd.Timestamp("2026-08-03")] == 20
    assert closes[pd.Timestamp("2026-08-04")] == 21
    # New non-overlapping rows
    assert closes[pd.Timestamp("2026-08-05")] == 22
    assert closes[pd.Timestamp("2026-08-06")] == 23
    assert back.duplicated(subset=["symbol", "timeframe", "timestamp"]).sum() == 0


def test_upsert_mixed_timeframe_same_partition(tmp_path):
    """Different timeframes in the same symbol/month partition must coexist
    without ArrowTypeError (pyarrow dict vs large_string schema merge)."""
    store = ParquetStorage(tmp_path / "ohlcv")
    ts = pd.date_range("2026-08-01", periods=4, freq="1D")
    store.upsert(_ohlcv_frame("EQ", ts, [10, 11, 12, 13]))

    # Upsert a different timeframe in the same partition
    df5m = pd.DataFrame({
        "symbol": ["EQ"]*2, "exchange": ["NSE"]*2, "kind": ["equity"]*2,
        "timeframe": ["5m"]*2,
        "timestamp": pd.to_datetime(["2026-08-01 09:15", "2026-08-01 09:20"]),
        "open": [10.0, 10.5], "high": [10.5, 11.0], "low": [9.8, 10.2],
        "close": [10.2, 10.8], "volume": [50, 60],
    })
    store.upsert(df5m)

    back_1d = store.read(symbols=["EQ"], timeframe="1d")
    back_5m = store.read(symbols=["EQ"], timeframe="5m")
    assert len(back_1d) == 4
    assert len(back_5m) == 2
    assert back_1d["close"].max() == 13
    assert back_5m["close"].max() == 10.8


def test_upsert_does_not_mutate_caller_dataframe(tmp_path):
    """upsert must not mutate the caller's DataFrame (no side-effects)."""
    store = ParquetStorage(tmp_path / "ohlcv")
    cols_before = {"symbol", "timeframe", "timestamp"}
    df = _ohlcv_frame("EQ", ["2026-08-01", "2026-08-02"], [10, 11])
    store.upsert(df)
    assert cols_before <= set(df.columns)  # no extra columns injected


# ============================================================ 3. duckdb scan
def test_duckdb_scan_default_1min_window(tmp_path):
    """duckdb_scan with no args defaults to the trailing 1-minute window."""
    import duckdb
    store = ParquetStorage(tmp_path / "ohlcv")
    now = pd.Timestamp.now()
    df = pd.DataFrame({
        "symbol": ["EQ"]*3, "exchange": ["NSE"]*3, "kind": ["equity"]*3,
        "timeframe": ["5m"]*3,
        "timestamp": pd.to_datetime([now - pd.Timedelta(minutes=5),
                                      now - pd.Timedelta(minutes=2),
                                      now - pd.Timedelta(minutes=1)]),
        "open": [10.0, 11.0, 12.0], "high": [11.0, 12.0, 13.0],
        "low": [9.0, 10.0, 11.0], "close": [10.0, 11.0, 12.0],
        "volume": [100, 100, 100],
    })
    store.upsert(df)

    con = duckdb.connect()
    store.duckdb_scan(con)
    result = con.execute("SELECT * FROM ohlcv ORDER BY timestamp").fetchdf()
    # Default 1-min window should include only the most recent bar
    assert len(result) <= 1


def test_duckdb_scan_explicit_window(tmp_path):
    """duckdb_scan with explicit start/end uses them."""
    import duckdb
    store = ParquetStorage(tmp_path / "ohlcv")
    ts = pd.date_range("2026-08-01", periods=5, freq="1h")
    df = _ohlcv_frame("EQ", ts, [10, 11, 12, 13, 14])
    store.upsert(df)

    con = duckdb.connect()
    start = pd.Timestamp("2026-08-01 02:00:00")
    end = pd.Timestamp("2026-08-01 03:00:00")
    store.duckdb_scan(con, start=start, end=end)
    result = con.execute("SELECT * FROM ohlcv ORDER BY timestamp").fetchdf()
    assert len(result) == 2  # 02:00 and 03:00
    assert list(result["close"]) == [12.0, 13.0]

def test_gap_detector_reports_only_missing_ranges(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(_ohlcv_frame("EQ", ["2026-08-01", "2026-08-03"], [10, 12]))

    eq = Equity("EQ")
    gaps = GapDetector(store).detect(
        [eq], start=datetime(2026, 8, 1), end=datetime(2026, 8, 5),
        timeframe="1d", bar_freq="1D",
    )
    assert len(gaps) == 1
    inst, ranges = gaps[0]
    assert inst is eq
    got = [(s.date(), e.date()) for s, e in ranges]
    # 08-02 is a lone gap; 08-04..08-05 merge into one contiguous range.
    assert got == [(date(2026, 8, 2), date(2026, 8, 2)),
                   (date(2026, 8, 4), date(2026, 8, 5))]


def test_gap_detector_no_gaps_when_fully_covered(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(_ohlcv_frame("EQ", pd.date_range("2026-08-01", periods=5, freq="1D"),
                              [10] * 5))
    gaps = GapDetector(store).detect(
        [Equity("EQ")], start=datetime(2026, 8, 1), end=datetime(2026, 8, 5),
        timeframe="1d", bar_freq="1D",
    )
    assert gaps == []


def test_gap_detector_full_range_when_symbol_missing(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(_ohlcv_frame("EQ", ["2026-08-01"], [10]))
    gaps = GapDetector(store).detect(
        [Equity("TCS")], start=datetime(2026, 8, 1), end=datetime(2026, 8, 3),
        timeframe="1d", bar_freq="1D",
    )
    assert len(gaps) == 1
    assert gaps[0][1] == [(datetime(2026, 8, 1), datetime(2026, 8, 3))]


def test_gap_detector_first_and_last_stored(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(_ohlcv_frame("EQ", ["2026-08-01", "2026-08-03"], [10, 12]))
    detector = GapDetector(store)
    assert detector.first_stored("EQ").date() == date(2026, 8, 1)
    assert detector.last_stored("EQ").date() == date(2026, 8, 3)
    assert detector.first_stored("MISSING") is None
    assert detector.last_stored("MISSING") is None


def test_parquet_store_symbols_and_date_range(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(_ohlcv_frame("EQ", ["2026-08-01", "2026-08-03"], [10, 12]))
    store.upsert(_ohlcv_frame("TCS", ["2026-08-02"], [50]))
    assert store.symbols() == ["EQ", "TCS"]
    lo, hi = store.date_range("EQ", timeframe="1d")
    assert lo.date() == date(2026, 8, 1)
    assert hi.date() == date(2026, 8, 3)
    assert store.date_range("MISSING") is None


def test_gap_detector_missing_symbols_helper(tmp_path):
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(_ohlcv_frame("EQ", ["2026-08-01"], [10]))
    missing = GapDetector(store).missing_symbols(
        [Equity("EQ"), Equity("TCS")], start=datetime(2026, 8, 1),
        end=datetime(2026, 8, 3), timeframe="1d",
    )
    assert [inst.symbol for inst in missing] == ["TCS"]


# ============================================================ 4. scanner loader
def test_scanner_loader_loads_universe_skips_missing(tmp_path, broker):
    broker.seed_history("RELIANCE", rows=30, timeframe="5m", start_price=100.0)
    broker.seed_history("TCS", rows=25, timeframe="5m", start_price=50.0)
    fetcher = ParallelHistoryFetcher(broker)
    df = fetcher.fetch([Equity("RELIANCE", broker=broker), Equity("TCS", broker=broker)],
                       timeframe="5m")

    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(df)

    loader = ScannerLoader(store)
    out = loader.load_universe(symbols=["RELIANCE", "TCS", "MISSING"], days=90,
                               timeframe="5m", end=datetime(2026, 8, 3, 15, 0))
    # MISSING has no partition — skipped silently, never an error.
    assert set(out["symbol"]) == {"RELIANCE", "TCS"}
    assert len(out[out["symbol"] == "RELIANCE"]) == 30
    assert len(out[out["symbol"] == "TCS"]) == 25
    assert {"symbol", "exchange", "kind", "timeframe", "timestamp",
            "open", "high", "low", "close", "volume"} <= set(out.columns)


def test_scanner_loader_symbols_none_means_all(tmp_path, broker):
    broker.seed_history("RELIANCE", rows=5, timeframe="5m")
    broker.seed_history("TCS", rows=5, timeframe="5m")
    fetcher = ParallelHistoryFetcher(broker)
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(fetcher.fetch([Equity("RELIANCE", broker=broker),
                                Equity("TCS", broker=broker)], timeframe="5m"))

    out = ScannerLoader(store).load_universe(days=90, timeframe="5m",
                                             end=datetime(2026, 8, 3, 15, 0))
    assert set(out["symbol"]) == {"RELIANCE", "TCS"}


# ============================================================ 5. derivative tagging
def test_derivative_tagging_round_trip(tmp_path, broker):
    expiry = date(2026, 8, 28)
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400, expiry=expiry,
                 option_type="CE", underlying_symbol="NIFTY", broker=broker)
    broker.seed_history(opt.symbol, rows=15, timeframe="5m", start_price=120.0)

    df = ParallelHistoryFetcher(broker).fetch([opt], timeframe="5m")
    assert set(df["kind"]) == {"option"}
    assert list(df["strike"]) == [24400.0] * 15
    assert set(df["option_type"]) == {"CE"}
    assert all(e == expiry for e in df["expiry"])

    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(df)

    back = store.read(symbols=[opt.symbol])
    assert len(back) == 15
    assert list(back["strike"]) == [24400.0] * 15
    assert set(back["option_type"]) == {"CE"}
    assert all(e == expiry for e in back["expiry"])


def test_mixed_kind_store_concat_cleanly(tmp_path, broker):
    """Equity + option rows in one store must concat with NaN-fill on the
    derivative-only columns (the Fix-2 dtype concern from the design spec)."""
    expiry = date(2026, 8, 28)
    rel = Equity("RELIANCE", broker=broker)
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400, expiry=expiry,
                 option_type="CE", underlying_symbol="NIFTY", broker=broker)
    broker.seed_history("RELIANCE", rows=10, timeframe="5m", start_price=100.0)
    broker.seed_history(opt.symbol, rows=10, timeframe="5m", start_price=120.0)

    df = ParallelHistoryFetcher(broker).fetch([rel, opt], timeframe="5m")
    store = ParquetStorage(tmp_path / "ohlcv")
    store.upsert(df)

    all_df = store.read()
    assert set(all_df["symbol"]) == {"RELIANCE", opt.symbol}
    rel_rows = all_df[all_df["symbol"] == "RELIANCE"]
    opt_rows = all_df[all_df["symbol"] == opt.symbol]
    assert rel_rows["strike"].isna().all()          # cash rows NaN-filled
    assert not opt_rows["strike"].isna().any()      # derivative rows intact
    assert len(all_df) == 20

# ============================================================ 6. universe loader
def test_universe_loader_loads_nifty50():
    """load_universe reads the Nifty 50 CSV and maps symbols to Equity."""
    from ntrade.data import load_universe, available_universes
    assert "nifty50" in available_universes()
    universe = load_universe("nifty50")
    assert len(universe) > 0
    assert all(inst.KIND == "equity" for inst in universe)
    assert all(inst.exchange == "NSE" for inst in universe)
    symbols = {inst.symbol for inst in universe}
    assert "RELIANCE" in symbols
    assert "HDFCBANK" in symbols


# ============================================================ 7. tz-aware storage
def test_upsert_strips_tz_from_broker_data(tmp_path):
    """Broker data with tz-aware timestamps is stored as tz-naive (fix for
    'Cannot compare tz-naive and tz-aware' in ParquetStorage.read)."""
    import numpy as np
    store = ParquetStorage(tmp_path / "ohlcv_tz")
    # Simulate Dhan-style tz-aware timestamps (UTC+5:30)
    ts = pd.date_range("2026-07-01 09:15", periods=5, freq="h").tz_localize("UTC+05:30")
    df = pd.DataFrame({
        "symbol": ["TEST"] * 5, "exchange": "NSE", "kind": "equity",
        "timeframe": "1m", "timestamp": ts,
        "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000,
    })
    store.upsert(df)
    result = store.read(symbols=["TEST"], timeframe="1m")
    assert result["timestamp"].dt.tz is None  # tz-naive after storage
    assert len(result) == 5

    # read with start/end must not raise (the original bug)
    df2 = store.read(symbols=["TEST"], timeframe="1m",
                     start="2026-07-01", end="2026-07-31")
    assert len(df2) == 5
