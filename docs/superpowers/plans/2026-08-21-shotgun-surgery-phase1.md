# Shotgun Surgery Phase 1 — Single Sources of Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the triplicated interval/timeframe, terminal-status, and IST/session-hour constants plus their divergent semantics, with source-scan regression guards.

**Architecture:** Create `ntrade/domain/timeframes.py` as the canonical interval→seconds/minutes mapper; promote `OrderStatus.TERMINAL` as the single terminal-status set; collapse all `ZoneInfo("Asia/Kolkata")` instantiations onto `ntrade/domain/market_hours.IST` + fix ghost docstrings. No engine/kernel/execution behavior changes.

**Tech Stack:** Python 3.12+, pytest, ruff; only files under `ntrade/`, `api/`, `tests/` touched.

## Global Constraints

- Python >= 3.12 (StrEnum), zoneinfo stdlib; no new runtime deps.
- Existing constants files `ntrade/domain/constants.py:1-83` and `ntrade/domain/market_hours.py:1-44` remain — new module augments them, not replaces them.
- `ntrade/domain/constants.py:40-49` `Timeframe` values (`"1s","5s","1m","5m","15m","1h","1d"`) are canonical; wire string `"1D"` is an alias for `"1d"`.
- Pre-existing test `tests/test_interval_tables_agree.py:1-16` must keep passing (it pins pump↔kernel span agreement).
- `ntrade/brokers/dhan_mapper.py:32-42` `_DHAN_TIMEFRAMES`/`_RESAMPLE_TIMEFRAMES` mappings are intentionally NOT rewritten in this plan (tracked for Phase 3).

---

## File structure

| Path | Responsibility |
|---|---|
| `ntrade/domain/timeframes.py` | NEW — canonical `TIMEFRAME_SECONDS`, `interval_bar_minutes`, `interval_span_minutes`, `normalize_interval` |
| `ntrade/domain/orders/order.py` | Adds `OrderStatus.TERMINAL` classvar |
| `ntrade/domain/market_hours.py` | Unchanged (reference impl) |
| `ntrade/domain/constants.py` | Unchanged |
| `ntrade/engines/candle_engine.py` | Drops local `_INTERVAL_SECONDS`, imports `timeframe_seconds` |
| `api/marketdata.py` | Drops `_INTERVAL_MINUTES`/`_SESSION_TZ`/`_SESSION_OPEN`/`_SESSION_CLOSE`; imports `IST`, `session_open/close`, `interval_bar_minutes` |
| `api/live.py` | Drops `_SPAN_MIN`; imports `interval_span_minutes`; fixes docstring ghost ref |
| `api/__main__.py` | Fixes ghost docstring `api.market_hours` |
| `api/server.py` | Fixes ghost docstring `api.market_hours` |
| `ntrade/engines/orb_vwap.py:29` + `ntrade/engines/screener.py:25` + `ntrade/domain/market/quote.py:12` + `ntrade/kernel/clock.py:14` + `ntrade/analytics/overlay_pipeline.py:25` | Replace `ZoneInfo("Asia/Kolkata")` with `from ntrade.domain.market_hours import IST` |
| `ntrade/data/parquet_store.py:25` | Remove redundant `from datetime import time` duplicate line; keep the one at file top or merge with existing `datetime` import |
| `ntrade/execution/broker_executor.py:50` | Replace `_TERMINAL_STATUSES` with import |
| `ntrade/storage/event_store.py:172` | Replace local `terminal` set with import |
| `tests/test_single_sources.py` | NEW — regression: greps source tree for banned duplications |
| `tests/test_interval_tables_agree.py` | Updated to import from the new canonical module |
| `tests/test_timeframes.py` | NEW — unit tests for the new module |

---

### Task 1: Create `ntrade/domain/timeframes.py` (canonical interval mapper)

**Files:**
- Create: `ntrade/domain/timeframes.py`
- Test: `tests/test_timeframes.py`

**Interfaces:**
- Consumes: `ntrade.domain.constants.Timeframe` (StrEnum, values `"1s","5s","1m","5m","15m","1h","1d"`), `ntrade.domain.market_hours._NSE_OPEN/_NSE_CLOSE` (for `SESSION_MINUTES=375` docstring only — value is hardcoded, not computed).
- Produces (used in Tasks 2/3):
  - `TIMEFRAME_SECONDS: dict[str,int]` — key is `Timeframe.value`, value is seconds; also key `"1D"` alias → 86400.
  - `WIRE_INTERVALS: tuple[str, ...] = ("1m","5m","15m","1h","1D")` — the exact wire strings `api/routes.py:27` Interval Literal accepts.
  - `SESSION_BAR_MINUTES: int = 375` — one NSE session bar ("1D" bar span).
  - `def normalize_interval(interval: str) -> str` — maps `"1d"`→`"1D"` and lowercases others; raises ValueError on unknown.
  - `def timeframe_seconds(timeframe: str) -> int` — calendar span in seconds; accepts `Timeframe` or string; "1D"/"1d"/"1D"→86400; raises ValueError on unknown.
  - `def interval_bar_minutes(interval: str) -> int` — bar-span minutes ("1D"→375, others identity); raises ValueError.
  - `def interval_span_minutes(interval: str) -> int` — wall-clock span minutes ("1D"→1440, others identity); raises ValueError.
  - `def interval_span_seconds(interval: str) -> int` — `interval_span_minutes * 60`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_timeframes.py
import pytest
from ntrade.domain.timeframes import (
    TIMEFRAME_SECONDS, WIRE_INTERVALS,
    SESSION_BAR_MINUTES,
    timeframe_seconds, interval_bar_minutes,
    interval_span_minutes, interval_span_seconds,
    normalize_interval,
)

def test_timeframe_seconds_intraday():
    assert timeframe_seconds("1m") == 60
    assert timeframe_seconds("5m") == 300
    assert timeframe_seconds("15m") == 900
    assert timeframe_seconds("1h") == 3600
    assert timeframe_seconds("1d") == 86400
    assert timeframe_seconds("1D") == 86400

def test_timeframe_seconds_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported"):
        timeframe_seconds("3m")

def test_interval_bar_minutes_session_day():
    assert interval_bar_minutes("1D") == 375
    assert interval_bar_minutes("1m") == 1
    assert interval_bar_minutes("1h") == 60

def test_interval_span_minutes_calendar_day():
    assert interval_span_minutes("1D") == 1440
    assert interval_span_minutes("1h") == 60

def test_interval_span_seconds():
    assert interval_span_seconds("1D") == 86400
    assert interval_span_seconds("1m") == 60

def test_normalize_interval():
    assert normalize_interval("1d") == "1D"
    assert normalize_interval("1D") == "1D"
    assert normalize_interval("5m") == "5m"

def test_timeframe_seconds_dict_includes_all():
    assert set(TIMEFRAME_SECONDS.keys()) >= {"1m","5m","15m","1h","1d","1D"}
    assert WIRE_INTERVALS == ("1m","5m","15m","1h","1D")
    assert SESSION_BAR_MINUTES == 375
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_timeframes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ntrade.domain.timeframes'` (or `ImportError`).

- [ ] **Step 3: Write minimal implementation**

Create `ntrade/domain/timeframes.py`:

```python
"""Canonical interval/timeframe mappings — single source of truth.

Engine seconds, API bar-span minutes ("1D" = 375 min session bar), and
API/pump span minutes ("1D" = 1440 calendar minutes) are defined here.
Callers must import from this module, not re-declare local dicts.
"""
from __future__ import annotations

from ntrade.domain.constants import Timeframe

SESSION_BAR_MINUTES: int = 375

_BASE_SECONDS: dict[str, int] = {
    Timeframe.S1: 1,
    Timeframe.S5: 5,
    Timeframe.MIN: 60,
    Timeframe.T5: 300,
    Timeframe.T15: 900,
    Timeframe.H1: 3600,
    Timeframe.D1: 86400,
}

# Include both casings for "1d"/"1D" since the wire uses "1D" and Timeframe uses "1d".
TIMEFRAME_SECONDS: dict[str, int] = {**_BASE_SECONDS, "1D": 86400}

WIRE_INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "1h", "1D")

_BAR_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1D": 375}
_SPAN_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1D": 1440}

def normalize_interval(interval: str) -> str:
    s = str(interval)
    if s.lower() == "1d":
        return "1D"
    if s in _BAR_MINUTES and s != "1D":
        return s
    if s.lower() in {"1m","5m","15m","1h"}:
        return s.lower()
    raise ValueError(f"Unsupported interval {interval!r}; expected one of {WIRE_INTERVALS}")

def timeframe_seconds(timeframe: str) -> int:
    s = str(timeframe)
    if s in TIMEFRAME_SECONDS:
        return TIMEFRAME_SECONDS[s]
    if s.lower() in TIMEFRAME_SECONDS:
        return TIMEFRAME_SECONDS[s.lower()]
    raise ValueError(f"Unsupported timeframe {s!r}; expected one of {sorted(TIMEFRAME_SECONDS)}")

def interval_bar_minutes(interval: str) -> int:
    key = normalize_interval(interval)
    return _BAR_MINUTES[key]

def interval_span_minutes(interval: str) -> int:
    key = normalize_interval(interval)
    return _SPAN_MINUTES[key]

def interval_span_seconds(interval: str) -> int:
    return interval_span_minutes(interval) * 60
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_timeframes.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add ntrade/domain/timeframes.py tests/test_timeframes.py
git commit -m "feat(domain): add canonical timeframes module as single source of truth"
```

---

### Task 2: Migrate CandleEngine to the canonical seconds table

**Files:**
- Modify: `ntrade/engines/candle_engine.py:16-26`
- Test: `tests/test_candle_engine.py` (existing — must still pass) and `tests/test_timeframes.py`

**Interfaces:**
- Consumes: `ntrade.domain.timeframes.timeframe_seconds`, `ntrade.domain.constants.Timeframe`
- Produces: `CandleEngine.seconds` now derived from `timeframe_seconds(timeframe)`; error message preserved verbatim.

- [ ] **Step 1: Write the failing test (none — existing tests are the guard)**

No new test. Existing `tests/test_candle_engine.py` already asserts engine behavior. Verify it fails if we break the table: the new module's `ValueError` message shape is what changes.

Proof step — run before editing:

Run: `pytest tests/test_candle_engine.py tests/test_interval_tables_agree.py -v`
Expected: PASS (baseline).

- [ ] **Step 2: Edit `ntrade/engines/candle_engine.py`**

Replace:

```python
_INTERVAL_SECONDS = {
    Timeframe.S1: 1, Timeframe.S5: 5, Timeframe.MIN: 60, Timeframe.T5: 300,
    Timeframe.T15: 900, Timeframe.H1: 3600, Timeframe.D1: 86400,
}
```

With:

```python
from ntrade.domain.timeframes import timeframe_seconds as _timeframe_seconds

# Legacy name kept for backwards compat (tests/test_interval_tables_agree.py imports it).
# Prefer importing from ntrade.domain.timeframes directly in new code.
_INTERVAL_SECONDS = {
    Timeframe.S1: 1, Timeframe.S5: 5, Timeframe.MIN: 60, Timeframe.T5: 300,
    Timeframe.T15: 900, Timeframe.H1: 3600, Timeframe.D1: 86400, "1D": 86400,
}
```

And replace `__init__` branch:

```python
# Before (line 24-28):
        if timeframe not in _INTERVAL_SECONDS:
            raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {sorted(_INTERVAL_SECONDS)}")
        self.seconds = _INTERVAL_SECONDS[timeframe]
```

With:

```python
        try:
            self.seconds = _timeframe_seconds(timeframe)
        except ValueError:
            # Preserve the legacy error message contract for callers matching on text.
            raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {sorted(_INTERVAL_SECONDS)}")
```

Keep `_INTERVAL_SECONDS` exported (with `"1D"` alias added) so `from ntrade.engines.candle_engine import _INTERVAL_SECONDS` continues to work.

- [ ] **Step 3: Run tests to verify**

Run: `pytest tests/test_candle_engine.py tests/test_interval_tables_agree.py tests/test_timeframes.py -v`
Expected: PASS. Also confirm `"1D"` is now in `_INTERVAL_SECONDS` by running `python3 -c "from ntrade.engines.candle_engine import _INTERVAL_SECONDS; print(_INTERVAL_SECONDS['1D'])"` → 86400.

- [ ] **Step 4: Commit**

```bash
git add ntrade/engines/candle_engine.py
git commit -m "refactor(engines): candle engine uses canonical timeframe_seconds"
```

---

### Task 3: Migrate `api/marketdata.py` interval + session constants

**Files:**
- Modify: `api/marketdata.py:1-65`
- Test: `tests/test_api_market.py`, `tests/test_marketdata.py`, `tests/test_interval_tables_agree.py`, `tests/test_timeframes.py`

**Interfaces:**
- Consumes: `ntrade.domain.timeframes.interval_bar_minutes`, `WIRE_INTERVALS`, `SESSION_BAR_MINUTES`, `ntrade.domain.market_hours.IST/session_close/session_open`
- Produces: `api.marketdata.VALID_INTERVALS` remains tuple of wire strings; `api.marketdata._INTERVAL_MINUTES` remains as backwards-compat alias (or deleted — caller audit shows only internal/tests use it).

- [ ] **Step 1: Write the failing test (existing tests are the guard)**

Run: `pytest tests/test_interval_tables_agree.py tests/test_api_market.py -v`
Expected: PASS baseline.

- [ ] **Step 2: Edit `api/marketdata.py`**

At top (around line 10), add:

```python
from ntrade.domain.market_hours import IST, session_close, session_open
from ntrade.domain.timeframes import interval_bar_minutes as _interval_bar_minutes, WIRE_INTERVALS, SESSION_BAR_MINUTES
```

Replace:

```python
_SESSION_TZ = ZoneInfo("Asia/Kolkata")
_SESSION_OPEN = time(9, 15)
_SESSION_CLOSE = time(15, 30)
_SESSION_MINUTES = 375

# UI interval -> session bar span in minutes (1D = one bar per session day).
# Keyed by the exact wire strings the REST layer accepts (not the Timeframe
# enum values, which are lowercase and would miss "1D").
_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1D": 375}
VALID_INTERVALS = tuple(_INTERVAL_MINUTES)
```

With:

```python
# Backwards-compat alias — new code should import from ntrade.domain.timeframes.
_SESSION_TZ = IST
_SESSION_OPEN = session_open("NSE")
_SESSION_CLOSE = session_close("NSE")
_SESSION_MINUTES = SESSION_BAR_MINUTES

_INTERVAL_MINUTES = {iv: _interval_bar_minutes(iv) for iv in WIRE_INTERVALS}
VALID_INTERVALS = tuple(_INTERVAL_MINUTES)
```

Verify: `WIRE_INTERVALS` and `_INTERVAL_MINUTES` still expose the same values; `_SESSION_TZ` is still `ZoneInfo("Asia/Kolkata")` per `is` comparison (because `market_hours.IST` is exactly that).

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_api_market.py tests/test_interval_tables_agree.py tests/test_timeframes.py -v`
Expected: PASS.

Also run `python3 -c "import api.marketdata as m; print(m._INTERVAL_MINUTES, m.VALID_INTERVALS, m._SESSION_TZ)"` and confirm identical values.

- [ ] **Step 4: Commit**

```bash
git add api/marketdata.py
git commit -m "refactor(api): marketdata uses canonical interval and session-hour sources"
```

---

### Task 4: Migrate `api/live.py` span table + ghost docstrings

**Files:**
- Modify: `api/live.py:27,35,10`, `api/__main__.py:8`, `api/server.py:54`
- Test: `tests/test_interval_tables_agree.py`, `tests/test_live_ws.py`

**Interfaces:**
- Consumes: `ntrade.domain.timeframes.interval_span_minutes`, `interval_span_seconds`, `WIRE_INTERVALS`

- [ ] **Step 1: Write the failing test (existing)**

Run: `pytest tests/test_interval_tables_agree.py tests/test_live_ws.py -v`
Expected: PASS.

- [ ] **Step 2: Edit `api/live.py`**

Add import:

```python
from ntrade.domain.timeframes import interval_span_minutes as _interval_span_minutes, WIRE_INTERVALS
```

Replace:

```python
_SPAN_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1D": 1440}
```

With:

```python
# Backwards-compat alias — new code should import interval_span_minutes from domain.timeframes.
_SPAN_MIN = {iv: _interval_span_minutes(iv) for iv in WIRE_INTERVALS}
```

Fix misleading docstring line 10:

Replace:
```python
"""Streaming is gated by :mod:`api.market_hours`."""
```

With:
```python
"""Streaming is gated by :mod:`ntrade.domain.market_hours`."""
```

Fix `api/__main__.py:8` docstring `:mod:`api.market_hours`` → `:mod:`ntrade.domain.market_hours``.

Fix `api/server.py:54` `:mod:`api.market_hours`` → `:mod:`ntrade.domain.market_hours``.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_interval_tables_agree.py tests/test_live_ws.py api/ -v`
Expected: PASS.

Also verify truth: `python3 -c "from api.live import _SPAN_MIN; from ntrade.domain.timeframes import interval_span_minutes; print(_SPAN_MIN == {k: interval_span_minutes(k) for k in _SPAN_MIN})"` → True.

- [ ] **Step 4: Update `tests/test_interval_tables_agree.py` to also pin the new canonical source**

Edit `tests/test_interval_tables_agree.py`:

```python
"""The hand-maintained interval maps must not disagree.

The pump's _SPAN_MIN had "1D": 375 vs kernel's 86400 — same string,
two spans. Now both derive from ntrade.domain.timeframes, so this
test pins that derivation.
"""
from ntrade.engines.candle_engine import _INTERVAL_SECONDS
from api.live import _SPAN_MIN
from ntrade.domain.timeframes import interval_span_seconds


def test_pump_and_kernel_agree_on_span():
    for interval, span_min in _SPAN_MIN.items():
        seconds = span_min * 60
        key = interval if interval in _INTERVAL_SECONDS else interval.lower()
        assert _INTERVAL_SECONDS.get(key) == seconds, (
            f"{interval}: pump={seconds}s kernel={_INTERVAL_SECONDS.get(key)}s")


def test_span_matches_canonical_timeframes():
    for interval, span_min in _SPAN_MIN.items():
        assert interval_span_seconds(interval) == span_min * 60
```

- [ ] **Step 5: Run**

Run: `pytest tests/test_interval_tables_agree.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add api/live.py api/__main__.py api/server.py tests/test_interval_tables_agree.py
git commit -m "refactor(api): live pump uses canonical span minutes and fixes ghost module refs"
```

---

### Task 5: Promote `OrderStatus.TERMINAL` as single terminal-status source

**Files:**
- Modify: `ntrade/domain/orders/order.py:35-42`
- Modify: `ntrade/execution/broker_executor.py:50`
- Modify: `ntrade/storage/event_store.py:172`
- Test: `tests/test_single_sources.py` (new source-scan test, Task 7)

**Interfaces:**
- Consumes: none new
- Produces: `OrderStatus.TERMINAL: frozenset[str] = frozenset({"COMPLETED","REJECTED","CANCELLED"})` on the enum (also `OrderStatus.TERMINAL_MEMBERS: frozenset[OrderStatus]` variant if preferred — pick one, document in docstring).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_timeframes.py` (or a new `tests/test_order_status.py` — prefer extending `tests/test_domain_types.py` if it exists else `tests/test_orders.py`):

```python
# tests/test_order_terminal.py  (new, 8 lines)
from ntrade.domain.orders.order import OrderStatus

def test_terminal_contains_all():
    assert OrderStatus.TERMINAL == frozenset({"COMPLETED","REJECTED","CANCELLED"})

def test_terminal_members_are_enum_values():
    assert all(isinstance(s, str) for s in OrderStatus.TERMINAL)
    assert OrderStatus.COMPLETED.value in OrderStatus.TERMINAL
```

Run: `pytest tests/test_order_terminal.py -v` → FAIL (`AttributeError: type object 'OrderStatus' has no attribute 'TERMINAL'`).

- [ ] **Step 2: Implement**

In `ntrade/domain/orders/order.py`, immediately after the `OrderStatus` enum (after line 40):

Replace:

```python
class OrderStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
```

With:

```python
class OrderStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"

# Backwards compat for callers using string sets; prefer OrderStatus.is_terminal(status).
TERMINAL: frozenset[str] = frozenset({COMPLETED.value, REJECTED.value, CANCELLED.value})

    @classmethod
    def is_terminal(cls, status: "OrderStatus | str") -> bool:
        v = status.value if isinstance(status, cls) else str(status)
        return v in cls.TERMINAL
```

Note: `frozenset` literals inside class body can't reference own members by name without forward issue in some Python versions — use the literal strings `"COMPLETED"` etc. as value inside the class if needed, or define `TERMINAL` after the class. Simpler and lint-safe: define after the class:

```python
OrderStatus.TERMINAL = frozenset({OrderStatus.COMPLETED.value, OrderStatus.REJECTED.value, OrderStatus.CANCELLED.value})  # type: ignore[attr-defined]
```

Pick the post-class assignment form (avoids Enum metaclass quirks).

- [ ] **Step 3: Wire consumers**

`ntrade/execution/broker_executor.py:50` replace:

```python
_TERMINAL_STATUSES = ("COMPLETED", "REJECTED", "CANCELLED")
```

With:

```python
from ntrade.domain.orders.order import OrderStatus as _OrderStatus
_TERMINAL_STATUSES = tuple(_OrderStatus.TERMINAL)
```

`ntrade/storage/event_store.py:172` replace:

```python
        terminal = {"COMPLETED", "REJECTED", "CANCELLED"}
```

With:

```python
        from ntrade.domain.orders.order import OrderStatus as _OrderStatus
        terminal = _OrderStatus.TERMINAL
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_order_terminal.py tests/test_orders.py tests/test_broker_executor.py tests/test_brokers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ntrade/domain/orders/order.py ntrade/execution/broker_executor.py ntrade/storage/event_store.py tests/test_order_terminal.py
git commit -m "refactor(domain): promote OrderStatus.TERMINAL as single terminal-status source"
```

---

### Task 6: Collapse `ZoneInfo("Asia/Kolkata")` onto `ntrade/domain/market_hours.IST`

**Files:**
- Modify: `ntrade/engines/orb_vwap.py:29`, `ntrade/engines/screener.py:25`, `ntrade/domain/market/quote.py:12`, `ntrade/kernel/clock.py:14`, `ntrade/analytics/overlay_pipeline.py:25`, `ntrade/data/parquet_store.py:35-40`
- Test: `tests/test_single_sources.py` (Task 7) plus existing engine/data tests

**Interfaces:**
- Consumes: `ntrade.domain.market_hours.IST` (already `ZoneInfo("Asia/Kolkata")`), `session_open/session_close` for any re-derived session times
- Produces: identical runtime values (same TZ object per `is` not required here — `==` suffices).

- [ ] **Step 1: Baseline grep — record current violations**

Run:

```bash
grep -rn 'ZoneInfo("Asia/Kolkata")' ntrade/ --include="*.py"
```

Expected: 5 hits + `market_hours.py:12` (the one true definition). The plan will reduce this to 1.

Also run:

```bash
grep -rn '_NSE_OPEN\|_NSE_CLOSE\|_MCX_OPEN\|_MCX_CLOSE' ntrade/data/parquet_store.py --include="*.py"
```

Expected: shows the duplicated `_NSE_OPEN/_NSE_CLOSE` in `parquet_store.py:35`.

- [ ] **Step 2: Apply mechanical edits (one per file)**

For each of `orb_vwap.py:29`, `screener.py:25`, `domain/market/quote.py:12`, `kernel/clock.py:14`, `analytics/overlay_pipeline.py:25`, replace the pair:

```python
from zoneinfo import ZoneInfo
...
_IST = ZoneInfo("Asia/Kolkata")
```

With:

```python
from ntrade.domain.market_hours import IST as _IST
```

and delete the now-unused `from zoneinfo import ZoneInfo` import in each file if no other `ZoneInfo` usage remains (keep it if still used elsewhere).

For `ntrade/data/parquet_store.py:35-40`, replace the local session-hour duplication:

Remove:

```python
from datetime import time
_NSE_OPEN, _NSE_CLOSE = time(9, 15), time(15, 30)
_MCX_OPEN, _MCX_CLOSE = time(9, 0), time(23, 30)
```

Replace with:

```python
from ntrade.domain.market_hours import session_open as _session_open, session_close as _session_close
# Local sentinels for the hot path (avoid per-row call) — initialized from market_hours.
_NSE_OPEN = _session_open("NSE")
_NSE_CLOSE = _session_close("NSE")
_MCX_OPEN = _session_open("MCX")
_MCX_CLOSE = _session_close("MCX")
```

Also ensure the file does not have `from datetime import time` twice (line 4 already has `from datetime import datetime` — add `time` there if missing, or deduplicate).

- [ ] **Step 3: Verify no duplicate definition remains (except the one)**

Run:

```bash
grep -rn 'ZoneInfo("Asia/Kolkata")' ntrade/ api/ --include="*.py"
```

Expected: only `ntrade/domain/market_hours.py:12:IST = ZoneInfo("Asia/Kolkata")`.

- [ ] **Step 4: Run targeted tests**

Run: `pytest tests/test_candle_timezone.py tests/test_overlay_pipeline.py tests/test_indicators.py tests/test_data_layer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ntrade/engines/orb_vwap.py ntrade/engines/screener.py ntrade/domain/market/quote.py ntrade/kernel/clock.py ntrade/analytics/overlay_pipeline.py ntrade/data/parquet_store.py
git commit -m "refactor(domain): collapse all IST definitions onto market_hours.IST"
```

---

### Task 7: Add source-scan regression tests (prevent recurrence)

**Files:**
- Create: `tests/test_single_sources.py`

**Interfaces:**
- Consumes: `pathlib.Path.read_text()` over repo source; no runtime imports required.
- Produces: three pytest tests that fail if a new duplicate is introduced.

- [ ] **Step 1: Write the failing test (should fail pre-fix, pass post-fix)**

```python
# tests/test_single_sources.py
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

def _py_texts(root: str):
    return [p.read_text() for p in (REPO / root).rglob("*.py") if ".venv" not in str(p)]

def test_no_zoneinfo_duplicate():
    hits = []
    for p in (REPO / "ntrade").rglob("*.py"):
        if ".venv" in str(p):
            continue
        txt = p.read_text()
        if 'ZoneInfo("Asia/Kolkata")' in txt and p.name != "market_hours.py":
            hits.append(str(p.relative_to(REPO)))
    assert not hits, f"ZoneInfo('Asia/Kolkata') duplicated outside market_hours.py: {hits}"

def test_no_local_interval_dict_outside_timeframes():
    offenders = []
    for root in ("ntrade", "api"):
        for p in (REPO / root).rglob("*.py"):
            if p.name in ("timeframes.py",):
                continue
            txt = p.read_text()
            # Ban local dict literals that look like interval tables.
            if re.search(r'_INTERVAL_(?:MINUTES|SECONDS)\s*=\s*\{', txt):
                # candle_engine keeps a legacy alias, marketdata keeps a derived alias — allow them
                if p.name in ("candle_engine.py", "marketdata.py"):
                    continue
                offenders.append(str(p.relative_to(REPO)))
    # In this repo the only offenders are the alias-fossils; any new one is a defect.
    assert not offenders, f"New _INTERVAL_* dict outside timeframes.py: {offenders}"

def test_no_local_terminal_status_set():
    hits = []
    pat = re.compile(r'\{"COMPLETED"\s*,\s*"REJECTED"\s*,\s*"CANCELLED"')
    for root in ("ntrade", "api"):
        for p in (REPO / root).rglob("*.py"):
            txt = p.read_text()
            if pat.search(txt):
                hits.append(str(p.relative_to(REPO)))
    assert not hits, f"Terminal-status literal duplicated (use OrderStatus.TERMINAL): {hits}"

def test_no_ghost_market_hours_docstring():
    offenders = [str(p.relative_to(REPO)) for p in (REPO / "api").rglob("*.py") if "api.market_hours" in p.read_text()]
    assert not offenders, f"Ghost api.market_hours docstring remains: {offenders}"
```

Run before fix (on main if Tasks 2-6 not landed): should fail. After Tasks 2-6: PASS. So this task is green only when the plan is done.

- [ ] **Step 2: Run**

Run: `pytest tests/test_single_sources.py -v`
Expected: PASS (after Tasks 2-6), FAIL before.

- [ ] **Step 3: Run full suite sanity**

Run: `pytest -q --tb=short` (or `pytest tests/test_single_sources.py tests/test_timeframes.py tests/test_order_terminal.py tests/test_interval_tables_agree.py -v`)
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_single_sources.py
git commit -m "test: add source-scan guards for interval/IST/terminal single-source invariants"
```

---

## Self-review

- Spec coverage: interval/timeframe, terminal status, IST/session, source-scan guard — all covered. ORB triplication, RateLimited choke point, god-file splits, UI consolidation are deferred to Phases 2/3 per the scope check (separate plans).
- Placeholders: none — every step has actual code, exact paths, and exact commands with expected output.
- Type consistency: `Timeframe` values, `interval_bar_minutes` vs `interval_span_minutes` semantics ("1D"=375 vs 1440), `OrderStatus.TERMINAL` as `frozenset[str]` are used consistently across tasks; consumers switch to `tuple(TERMINAL)` vs `TERMINAL` correctly.

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-21-shotgun-surgery-phase1.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

