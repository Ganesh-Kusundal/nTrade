# Design: Parallel Historical Fetch + Parquet Storage + Gap Detection

> **Status: v2 (reviewed 2026-08-03)** — v1 claimed deps were installed and a
> 3-file layout; reality was 4 files with `scanner_loader.py` **missing** and
> `pyarrow`/`duckdb` **not installed**. This revision corrects the record and
> adds a concrete implementation plan tracked on the kanban board.

## Problem
Need to fetch historical OHLCV data for many instruments (equities, futures,
options) from a broker **in parallel**, store it in **partitioned Parquet**
(Hive-style: `symbol/time`), detect **gaps** automatically against a requested
symbol universe, and support **scanner workloads** loading 2–3 months of data
per symbol across 100s of symbols.

## Scope Decisions (from user)
- **Scope**: Both backfill + scanner reads
- **Storage layout**: Hive-style partition by `symbol` + `year`/`month`
- **Sync semantics**: Upsert — overwrite overlapping bars, dedupe on collision

## Existing Infrastructure (verified 2026-08-03)
| Component | Status | Verified |
|-----------|--------|----------|
| `BrokerAdapter.get_historical()` | ✓ Implemented (DhanBroker, PaperBroker) | `dhan.py:179`, `paper.py:78` — `(instrument, timeframe="5m", days=None, start=None, end=None)` |
| `DhanTransport._invoke(Quota.DATA, ...)` | ✓ Rate-limited at 5/s per Dhan docs | `rate_limit.py:44` → `Quota.DATA: ((1.0, 5), (86400.0, 100_000))` |
| `BrokerRateGate` | ✓ Thread-safe, shared per session, `status()` | `rate_limit.py:85,142` |
| `InstrumentFactory` | ✓ | `factories.py:20` |
| `SymbolMaster` | ✓ Flyweight instrument cache (thread-safe) | `registry.py:16` |
| `CandleSeries` | ✓ Domain wrapper around OHLCV DataFrame, has `to_dataframe()` | `candles.py:18,31` — fetcher's `hasattr` check binds it correctly |
| `duckdb` | ✗ **NOT installed** — must add to `pyproject.toml` + venv | `ModuleNotFoundError` |
| `pyarrow` | ✗ **NOT installed** — must add to `pyproject.toml` + venv | `ModuleNotFoundError` (this makes `import ntrade.data` fail today) |
| `pandas` | ✓ 3.0.5 (plan said 3.0.0, close) | installed |

## Architecture (4 files — as actually laid out)

```
ntrade/data/
├── __init__.py            # exports all four (imports scanner_loader → currently broken)
├── history_pipeline.py    # ParallelHistoryFetcher (+ fetch_missing)
├── parquet_store.py       # ParquetStorage (Hive partition + upsert)
├── gap_detector.py        # GapDetector (own module — v1 said it lived in history_pipeline)
└── scanner_loader.py      # ScannerLoader — ⚠ MISSING, must be implemented
```

### 1. ParallelHistoryFetcher (`history_pipeline.py`) — ✅ implemented
- `ThreadPoolExecutor(max_workers=N)` — worker count **hardcoded to 4**
  (`_MAX_WORKERS`), derived from the DATA quota window (5/s). v1 said "derived
  from `BrokerRateGate.status()`" — the constant is the documented intent;
  runtime derivation is optional polish, not a requirement.
- Each worker calls `broker.get_historical(inst, ...)` → transport →
  `BrokerRateGate` serializes to 5/s.
- Returns merged `pd.DataFrame` tagged `symbol, exchange, kind, timeframe` +
  `strike/option_type/expiry` for derivatives.
- `fetch_missing(insts, store, gap_detector, ...)` — gap-aware fetch using
  `GapDetector.detect()`, returns only missing ranges per instrument.
- Per-instrument errors are caught and logged; partial results still returned.

### 2. ParquetStorage (`parquet_store.py`) — ✅ implemented (2 fixes needed)
- Layout: `base_path/ohlcv/symbol={SYMBOL}/year={YYYY}/month={MM}/data.parquet`
- `upsert(df)` — deletes overlapping rows (same symbol+timeframe+timestamp)
  then appends; idempotent on re-fetch.
- `read(symbols, start, end, timeframe)` — Hive partition pruning + timestamp
  filter.
- `symbols()`, `date_range(symbol)`, `clear()` — metadata/test isolation.
- `duckdb_scan(con)` — optional DuckDB view over `parquet_scan(..., hive_partitioning=True)`.
- **Fix 1 (dead import)**: `from ntrade.execution.rate_limit import Quota` is
  unused — remove.
- **Fix 2 (column typing)**: `volume` is force-cast to `int64`; `strike` /
  `expiry` / `option_type` may be NaN for cash instruments — confirm
  `pa.Table.from_pandas` handles mixed dtypes across partitions (test will
  catch this).

### 3. GapDetector (`gap_detector.py`) — ✅ implemented
- `detect(instruments, start, end, timeframe, bar_freq)` → list of
  `(instrument, [(gap_start, gap_end), ...])`.
- `missing_symbols(...)` / `first_stored()` / `last_stored()` helpers.
- Builds an expected `pd.date_range` at `bar_freq` and diffs against stored
  timestamps. NOTE: expected grid is 24h-continuous — overnight/weekend
  timestamps will read as "missing". For 5m intraday data this over-reports
  gaps. Mitigation is acceptable for the initial use case (fetch full missing
  range per instrument); a trading-session-aware grid is a future refinement.

### 4. ScannerLoader (`scanner_loader.py`) — ⚠ MISSING, to be implemented
Wrap `ParquetStorage.read` for scanner throughput:

```python
class ScannerLoader:
    """Partition-pruned 2-3 month reads for scanner workloads."""

    def __init__(self, store: ParquetStorage):
        self._store = store

    def load_universe(self, symbols: list[str] | None = None,
                      days: int = 90, timeframe: str = "5m",
                      end: datetime | None = None) -> pd.DataFrame:
        """Load the trailing `days` per symbol in one in-memory frame.

        - `symbols=None` → all symbols in store
        - `end=None` → datetime.now()
        - Skips symbols with no data in the window (lazy, no error)
        - Returns columns symbol, exchange, kind, timeframe, timestamp,
          open, high, low, close, volume [, strike, option_type, expiry]
        """
```

Implementation is a thin call to `store.read(symbols, start=end - timedelta(days=days), end=end, timeframe=timeframe)` — partition pruning does the heavy lifting. No new logic needed beyond the API.

## Data Flow (End-to-End)
```python
fetcher = ParallelHistoryFetcher(broker)
universe = [Equity("RELIANCE"), Index("NIFTY"), Future(...), Option(...), ...]
df = fetcher.fetch(universe, timeframe="5m", start="2026-07-01", end="2026-07-31")

store = ParquetStorage("/data/ohlcv")
store.upsert(df)

# Gap detection: what's missing for next month?
gap_detector = GapDetector(store)
missing = gap_detector.detect(universe, start="2026-08-01", end="2026-08-31",
                              timeframe="5m", bar_freq="5min")
if missing:
    df2 = fetcher.fetch_missing(universe, store, gap_detector,
                                start="2026-08-01", end="2026-08-31")
    store.upsert(df2)

# Scanner: load 90 days for 100+ symbols
loader = ScannerLoader(store)
live_df = loader.load_universe(store.symbols()[:200], days=90)
```

Note: `ParallelHistoryFetcher` exposes `fetch` / `fetch_missing` only — gap
*detection* lives on `GapDetector.detect()`; there is no `detect_gaps`
method on the fetcher.

## Key Constraints
- **No mocking in tests** — use `PaperBroker` with seeded data
  (`paper.py` has `seed_history(symbol, timeframe=...)`).
- Zero data loss / zero duplication — upsert handles re-fetches idempotently.
- Scanner loads only recent partitions (partition pruning).
- Worker count respects broker rate limits (don't exceed DATA 5/s).

---

## Implementation Plan (kanban-tracked)

| Task | ID | Type | Scope |
|------|----|------|-------|
| Add `pyarrow` + `duckdb` to `pyproject.toml` deps + install in venv | T-038 | task | unblocks import |
| Implement `ntrade/data/scanner_loader.py` (ScannerLoader) | F-002 | feature | completes API |
| Clean dead `Quota` import in `parquet_store.py` | D-021 | debt | hygiene |
| Write `tests/test_data_layer.py` — PaperBroker round trip | T-039 | task | no-mock coverage |
| Refresh graphify knowledge graph to include `ntrade/data/` | T-040 | task | graph in sync |

Order: T-038 → F-002 → D-021 → T-039 → T-040. T-039 depends on T-038/F-002
(import must succeed, ScannerLoader must exist). T-040 (graphify update) runs
last so the graph reflects all new files.

## Test Plan (`tests/test_data_layer.py`)
1. **Round trip**: `PaperBroker` seeded → `ParallelHistoryFetcher.fetch` →
   `ParquetStorage.upsert` → `read` returns same bars.
2. **Idempotent upsert**: upsert same frame twice → no duplicate rows.
3. **Gap detection**: store partial range → `GapDetector.detect` reports the
   missing range only.
4. **ScannerLoader**: load 90 days over a multi-symbol store → correct rows,
   missing symbols skipped.
5. **Derivative tagging**: option instrument fetch carries
   `strike/option_type/expiry` through to parquet and back.
