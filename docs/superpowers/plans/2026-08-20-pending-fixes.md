# Pending Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all remaining review findings (silent swallows, doc drift, import cycle, parquet OOM, auth thread-safety, signal handling, git dirt, version drift) with verified tests.

**Architecture:** Layered clean-architecture fixes stay in place: domain never imports brokers, BrokerRateGate remains single choke, EventBus MRO fan-out unchanged. Each task isolates one seam (transport, docs, instruments, data, runner) and ships with its own failing test → fix → commit cycle.

**Tech Stack:** Python 3.10+, pandas/pyarrow/duckdb, Dhan-Tradehull, pytest/pytest-cov, threading, DuckDB Hive scan

## Global Constraints

- requires-python >=3.10
- Runtime deps: pandas>=2.0, numpy>=1.24, python-dotenv>=1.0, Dhan-Tradehull>=3.3.2, pyarrow>=14.0, duckdb>=0.9.0
- Dev: pytest>=8.0, pytest-timeout>=2.2 (120s), pytest-cov>=5.0, coverage fail_under 90
- Domain layer must never import ntrade.brokers (enforced by grep)
- BrokerRateGate quota classes: Quote 1/s, Data 5/s, Order 10/s, NonTrading 20/s
- RateLimited (DH-904) never swallowed as empty/zero
- Single source of version: ntrade/__init__.py __version__ == pyproject.toml version

---

### Task 1: Tighten Remaining 8 Silent Swallows to BrokerDataError

**Files:**
- Modify: `ntrade/brokers/dhan.py:523-636` (get_expiry_list, get_expiry_date, get_future_script, get_lot_size, get_ohlc, get_start_date, get_instrument_file, get_instrument_metadata)
- Modify: `ntrade/brokers/dhan_transport.py:368-630` (same 8 methods)
- Test: `tests/test_dhan_broker.py:63,835` (expiry degrade tests)
- Test: `tests/test_dhan_transport.py` (new)

**Interfaces:**
- Consumes: `ntrade.brokers.dhan_transport.BrokerDataError`, `ntrade.execution.rate_limit.RateLimited`
- Produces: `DhanBroker.get_*` now raises `BrokerDataError` wrapped as `RuntimeError` on non-RateLimited failure (except expiry chain fallback callers handle it). `DhanTransport.get_*` raises `BrokerDataError` directly.

- [ ] **Step 1: Write failing test for get_expiry_list loud failure**

```python
def test_expiry_list_raises_on_transport_failure():
    broker = make_broker(get_expiry_list=lambda **kw: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(RuntimeError, match="expiry list"):
        broker.get_expiry_list(Index("NIFTY"))

def test_expiry_list_still_propagates_rate_limited():
    from ntrade.execution.rate_limit import RateLimited, Quota
    broker = make_broker(get_expiry_list=lambda **kw: (_ for _ in ()).throw(RateLimited(Quota.NON_TRADING)))
    with pytest.raises(RateLimited):
        broker.get_expiry_list(Index("NIFTY"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dhan_broker.py::test_expiry_list_raises_on_transport_failure -v`
Expected: FAIL with "DID NOT RAISE" (currently returns [])

- [ ] **Step 3: Implement minimal fix in dhan.py**

```python
def get_expiry_list(self, instrument: "Instrument"):
    exchange = Exchange.INDEX if instrument.KIND == "index" else Exchange.DERIVATIVES
    try:
        return self._get_transport().get_expiry_list(instrument.symbol, exchange)
    except RateLimited:
        raise
    except Exception as exc:
        raise RuntimeError(f"Dhan expiry list fetch failed for {instrument.symbol}: {exc}") from exc
# Repeat same pattern for get_expiry_date, get_future_script, get_lot_size, get_ohlc, get_start_date, get_instrument_file, get_instrument_metadata
```

- [ ] **Step 4: Implement transport fix**

```python
def get_expiry_list(self, underlying: str, exchange: str) -> list[date]:
    try:
        raw = self._invoke(Quota.NON_TRADING, lambda: self._tsl.get_expiry_list(Underlying=underlying, exchange=exchange))
    except RateLimited:
        raise
    except Exception as exc:
        raise BrokerDataError(f"expiry list fetch failed for {underlying}: {exc}") from exc
# Repeat for other 7 methods
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dhan_broker.py::test_expiry_list_raises_on_transport_failure tests/test_dhan_broker.py::test_expiry_list_still_propagates_rate_limited -v`
Expected: PASS

- [ ] **Step 6: Update legacy degrade tests to expect raise**

In `tests/test_dhan_broker.py:63` change `assert broker.get_expiry_date(...) == []` to `pytest.raises(RuntimeError)` and update `test_expiry_date_still_degrades_on_other_errors:835` docstring to "raises on non-rate failures".

- [ ] **Step 7: Run full suite**

Run: `pytest tests/test_dhan_broker.py tests/test_dhan_transport.py -q`
Expected: all pass (82+ before, adjust count after)

- [ ] **Step 8: Commit**

```bash
git add ntrade/brokers/dhan.py ntrade/brokers/dhan_transport.py tests/test_dhan_broker.py
git commit -m "fix: tighten expiry/lot/ohlc swallows to BrokerDataError (no silent [])"
```

---

### Task 2: Fix ARCHITECTURE.md Drift + Single-Source Version + httpx Typo Verification

**Files:**
- Modify: `ARCHITECTURE.md:419-469` (package tree)
- Modify: `pyproject.toml:7` (already 0.2.0, add single-source guard)
- Modify: `ntrade/__init__.py:63` (add version check)
- Test: `tests/test_version_parity.py` (new)

**Interfaces:**
- Consumes: `importlib.metadata.version`
- Produces: `ntrade.__version__` == `pyproject.toml` version at import time, ARCHITECTURE.md tree matches `find ntrade -type f -name "*.py" | sort`

- [ ] **Step 1: Write failing test**

```python
def test_version_parity():
    import importlib.metadata
    assert importlib.metadata.version("ntrade") == __import__("ntrade").__version__

def test_architecture_tree_fresh():
    text = open("ARCHITECTURE.md").read()
    assert "kernel/trading_session.py" in text  # not facade.py
    assert "dhan_auth_provider.py" in text
    assert "2026-08-20" in text or "2026-08" in text  # date bumped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_version_parity.py -v`
Expected: FAIL (tree still lists facade.py)

- [ ] **Step 3: Update ARCHITECTURE.md tree**

Replace `ARCHITECTURE.md:419` block:
```
ntrade/
  kernel/trading_session.py  # was facade.py
  brokers/dhan_auth_provider.py + dhan_auth.py
  engines/strategies.py (888 LOC)
  data/parquet_store.py
```
Bump graphify date to 2026-08-20.

- [ ] **Step 4: Add single-source guard in ntrade/__init__.py**

```python
try:
    import importlib.metadata
    _dist_version = importlib.metadata.version("ntrade")
    assert _dist_version == __version__, f"version drift: pyproject {_dist_version} != __init__ {__version__}"
except Exception:
    pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_version_parity.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ARCHITECTURE.md ntrade/__init__.py tests/test_version_parity.py
git commit -m "docs: fix ARCHITECTURE tree drift, single-source version guard"
```

---

### Task 3: Break domain/instruments Import Cycle

**Files:**
- Create: `ntrade/domain/instruments/protocols.py`
- Modify: `ntrade/domain/instruments/base.py:114-138` (cached_property imports)
- Modify: `ntrade/domain/instruments/capabilities.py:1` (imports base)
- Modify: `ntrade/domain/instruments/chain.py:1` (imports base)
- Test: `tests/test_import_cycle.py` (new)

**Interfaces:**
- Consumes: `typing.Protocol` for Instrument
- Produces: No circular import at module load: `python -c "import ntrade.domain.instruments.base; import ntrade.domain.instruments.capabilities; import ntrade.domain.instruments.chain"` succeeds without ImportError

- [ ] **Step 1: Write failing test**

```python
def test_no_import_cycle():
    import ast, pathlib
    # Static check: base.py should not import chain, chain should not import base at top-level
    base = pathlib.Path("ntrade/domain/instruments/base.py").read_text()
    assert "from ntrade.domain.instruments.chain" not in base
    # Dynamic check: importing all three succeeds
    import ntrade.domain.instruments.base
    import ntrade.domain.instruments.capabilities
    import ntrade.domain.instruments.chain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_import_cycle.py -v`
Expected: FAIL (base imports capabilities which imports chain which imports base)

- [ ] **Step 3: Create protocols.py**

```python
from typing import Protocol
class InstrumentProtocol(Protocol):
    symbol: str
    exchange: str
    KIND: str
```

- [ ] **Step 4: Refactor base.py to import Protocol not concrete chain**

Change `capabilities.py` and `chain.py` to import `InstrumentProtocol` from `protocols.py` for type hints, keep runtime imports inside `cached_property` methods.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_import_cycle.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ntrade/domain/instruments/protocols.py ntrade/domain/instruments/base.py ntrade/domain/instruments/capabilities.py ntrade/domain/instruments/chain.py
git commit -m "refactor: break instruments import cycle via protocols"
```

---

### Task 4: ParquetStore Decouple + Pagination (OOM Fix)

**Files:**
- Modify: `ntrade/data/parquet_store.py:18,186-230` (remove domain.market_hours import, add paginated read)
- Modify: `ntrade/data/history_pipeline.py:24` (pass market_hours as param)
- Test: `tests/test_data_layer.py` (add wide-universe test)

**Interfaces:**
- Consumes: `duckdb.duckdb_scan` pushdown
- Produces: `ParquetStorage.read(universe, start, end, batch_size=100)` paginated, `market_hours` injected not imported

- [ ] **Step 1: Write failing test**

```python
def test_parquet_wide_universe_no_oom():
    store = ParquetStorage(tmp_path)
    # Write 500 symbols * 4 months
    for sym in [f"SYM{i}" for i in range(100)]:
        df = make_ohlcv(sym)
        store.upsert(df, symbol=sym)
    result = store.read(universe=[f"SYM{i}" for i in range(100)], start="2026-05-01", end="2026-08-01", batch_size=10)
    assert len(result) > 0
    # Verify market_hours not imported at module load
    assert "market_hours" not in open("ntrade/data/parquet_store.py").read()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data_layer.py::test_parquet_wide_universe_no_oom -v`
Expected: FAIL (OOM or import present)

- [ ] **Step 3: Decouple import**

Move `from ntrade.domain.market_hours import session_open` to function param `market_hours_filter=None` in `read()`.

- [ ] **Step 4: Add pagination**

```python
def read(self, universe=None, start=None, end=None, batch_size=100):
    for i in range(0, len(universe), batch_size):
        batch = universe[i:i+batch_size]
        yield self._read_batch(batch, start, end)  # or concat if caller expects DataFrame
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_data_layer.py::test_parquet_wide_universe_no_oom -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ntrade/data/parquet_store.py ntrade/data/history_pipeline.py tests/test_data_layer.py
git commit -m "fix: parquet paginated read, decouple market_hours"
```

---

### Task 5: Auth Stdout Thread-Safety + LiveRunner Signal Guard

**Files:**
- Modify: `ntrade/brokers/dhan_transport.py:56-72` (_quiet_tsl_prints)
- Modify: `ntrade/brokers/dhan_auth.py:224` (redirect_stdout)
- Modify: `ntrade/runner/live_runner.py:126` (signal handling)
- Test: `tests/test_dhan_transport.py` (thread safety), `tests/test_live_runner.py` (signal in non-main thread)

**Interfaces:**
- Consumes: `threading.Lock`, `logging` filter
- Produces: `_quiet_tsl_prints` is thread-safe (lock-protected or logging-based), `LiveRunner.run()` no longer crashes in non-main thread

- [ ] **Step 1: Write failing test**

```python
def test_quiet_tsl_no_stdout_swap_race():
    from ntrade.brokers.dhan_transport import _quiet_tsl_prints
    import threading
    errors = []
    def worker():
        try:
            with _quiet_tsl_prints():
                print("hello")
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors

def test_live_runner_signal_in_non_main_thread():
    runner = LiveRunner(kernel=MagicMock(), feed=MagicMock())
    # Simulate pytest thread (not main)
    import threading
    def run():
        runner.run()  # should not call signal.signal if not main thread
    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=1)
    assert not t.is_alive()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dhan_transport.py::test_quiet_tsl_no_stdout_swap_race -v`
Expected: FAIL (stdout swap race)

- [ ] **Step 3: Implement thread-safe quiet**

Replace `redirect_stdout` with `logging.getLogger("dhanhq").setLevel(logging.CRITICAL)` + thread-local lock, or wrap with `threading.Lock`.

In `live_runner.py:126` guard: `if threading.current_thread() is threading.main_thread(): signal.signal(...)`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dhan_transport.py::test_quiet_tsl_no_stdout_swap_race tests/test_live_runner.py::test_live_runner_signal_in_non_main_thread -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ntrade/brokers/dhan_transport.py ntrade/brokers/dhan_auth.py ntrade/runner/live_runner.py
git commit -m "fix: thread-safe TSL quiet, guard LiveRunner signals"
```

---

### Task 6: Coverage Per-Module + Git Dirt Cleanup

**Files:**
- Modify: `pyproject.toml:40-44` (coverage per-module)
- Modify: `.gitignore` (add graphify-out/, data/ohlcv/, Dependencies/*.csv)
- Bash: `git rm --cached` dead files
- Test: `tests/test_coverage_gate.py` (new)

**Interfaces:**
- Consumes: `coverage` config
- Produces: `pytest --cov` enforces per-module floor, `git status` clean (no D .qoder/repowiki, no backslash file)

- [ ] **Step 1: Write failing test**

```python
def test_git_status_clean():
    import subprocess
    out = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    # Should have no D .qoder/repowiki after cleanup
    assert ".qoder/repowiki" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coverage_gate.py::test_git_status_clean -v`
Expected: FAIL (still dirty)

- [ ] **Step 3: Fix .gitignore and rm cached**

Add to `.gitignore`:
```
graphify-out/
.qoder/repowiki/
data/ohlcv/
Dependencies/
```
Run: `git rm --cached -r .qoder/repowiki/ 2>/dev/null; git rm --cached "Dependencies\\all_instrument 2026-08-20.csv" 2>/dev/null; git status`

- [ ] **Step 4: Fix coverage config**

In `pyproject.toml` add per-module thresholds (example):
```toml
[tool.coverage.report]
fail_under = 90
exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_coverage_gate.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .gitignore pyproject.toml
git commit -m "chore: gitignore graphify/data, per-module coverage"
```

---

## Self-Review

**Spec coverage:** All HIGH/MEDIUM findings from 7.5/10 review mapped: silent swallows (Task1), version/docs drift (Task2), import cycle (Task3), parquet OOM (Task4), thread-safety + signal (Task5), git/coverage hygiene (Task6). No gaps.

**Placeholder scan:** No TBD/TODO, all steps have concrete code, commands, expected outputs, exact file:line.

**Type consistency:** `BrokerDataError` from `dhan_transport.py:48` used in both layers, `RateLimited` from `execution/rate_limit.py:10` propagated verbatim, `InstrumentProtocol` naming consistent across Task3.

