# Deeper Cleanup (Tier B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining provably-redundant / not-in-active-flow code from the nTrade codebase: the `api/market_hours.py` re-export shim and the self-contained research/one-off `scripts/`, while preserving every active execution path and every tested module.

**Architecture:** Two independent, separately-reviewable deletions. Task 1 collapses a pure re-export file into its canonical source (zero behavioral change, call-site reroute). Task 2 deletes research/one-off scripts that are not imported by any production or test code and are not part of the active trading/serving flow — the active entry points (`api/` server, `scripts/pre_deploy_check.py`, `scripts/*_backfill*.py`, `scripts/live_*.py`, `scripts/paper_gate_run.py`, `scripts/download_nifty_universe.py`) are explicitly retained.

**Tech Stack:** Python 3.12 (`.venv`), pytest, FastAPI (`api/`), DuckDB (`ntrade/data`).

## Global Constraints

- **Real-money system:** No deletion that changes runtime behavior of any active path. Every removed symbol must be proven unreferenced by production or test code.
- **No `valentini.ts` deletion:** `ui/src/lib/valentini.ts` is actively tested (`ui/src/lib/__tests__/valentini.test.ts`, `marketHours.test.ts`) and tracked in `PONYTAIL-DEBT.md` — it is NOT dead, despite an earlier survey flag. Excluded from this plan.
- **No production entry points deleted:** `api/server.py`, `api/__main__.py`, and the 9 ACTIVE_ENTRY scripts (see Task 2 keep-list) stay.
- **Verification gate:** After each task, the relevant test subset must pass and `import ntrade` + `import api.server` must succeed.
- **Frequent commits:** one commit per task, message prefixed `cleanup:`.

---

### Task 1: Remove `api/market_hours.py` re-export shim

**Files:**
- Delete: `api/market_hours.py`
- Modify: `api/live.py:24` (import `IST, is_market_open, session_open` from `ntrade.domain.market_hours`)
- Modify: `api/paper_trader.py:30` (import `IST` from `ntrade.domain.market_hours`)
- Modify: `tests/test_live_ws.py:11` (import `IST, is_market_open` from `ntrade.domain.market_hours`)
- Test: `tests/test_live_ws.py`, `tests/test_marketdata.py`

**Interfaces:**
- Consumes: `ntrade.domain.market_hours.IST`, `is_market_open`, `session_open`, `session_close` (already the canonical source; `api/market_hours.py` only re-exported these).
- Produces: no new symbols; removes `api.market_hours` module entirely.

- [ ] **Step 1: Update `api/live.py` import**

Replace:
```python
from api.market_hours import IST, is_market_open, session_open
```
with:
```python
from ntrade.domain.market_hours import IST, is_market_open, session_open
```

- [ ] **Step 2: Update `api/paper_trader.py` import**

Replace:
```python
from api.market_hours import IST
```
with:
```python
from ntrade.domain.market_hours import IST
```

- [ ] **Step 3: Update `tests/test_live_ws.py` import**

Replace:
```python
from api.market_hours import IST, is_market_open
```
with:
```python
from ntrade.domain.market_hours import IST, is_market_open
```

- [ ] **Step 4: Delete the shim**

```bash
rm api/market_hours.py
```

- [ ] **Step 5: Verify imports + tests**

Run:
```bash
.venv/bin/python -c "import api.server, api.live, api.paper_trader; print('api OK')"
.venv/bin/python -m pytest tests/test_live_ws.py tests/test_marketdata.py -q
```
Expected: `api OK`, all tests PASS, zero reference to `api.market_hours` remains.

- [ ] **Step 6: Commit**

```bash
git add api/live.py api/paper_trader.py tests/test_live_ws.py
git rm api/market_hours.py
git commit -m "cleanup: collapse api/market_hours re-export shim into ntrade.domain.market_hours"
```

---

### Task 2: Delete self-contained research / one-off `scripts/`

**Context:** A prior consolidation (Tier A) already folded the duplicated FIFO-PnL / parquet-load helpers into `scripts/_sweep_common.py` and rerouted `cost_breakdown.py`, `sweep_chandelier_trail.py`, `backtest_morning_vah_val.py` to use it. The research scripts below are not imported by any production or test code (verified: `grep -rln` for `from scripts` / `import sweep*` / `import backtest_*` outside `scripts/` returns nothing), each has its own `__main__` (or reads a `/tmp` CSV), and all compile against the current `ntrade` API. They are research/reproducibility artifacts, not part of the active execution flow.

**Delete (15 files):**
```
scripts/_sweep_common.py
scripts/sweep_morning_vah_val.py
scripts/sweep_morning_vah_val2.py
scripts/sweep_morning_vah_val3.py
scripts/sweep_chandelier_trail.py
scripts/analyze_sweep2.py
scripts/backtest_morning_vah_val.py
scripts/backtest_valentini.py
scripts/cost_breakdown.py
scripts/benchmark_fetch.py
scripts/benchmark_latency.py
scripts/ema_cross_run.py
scripts/live_runner_run.py
scripts/dev_serve_api.py
scripts/apply_graph_resolutions.py
```

**KEEP (active entry points / operational — do NOT delete):**
```
scripts/pre_deploy_check.py
scripts/paper_gate_run.py
scripts/live_read_check.py
scripts/live_smoke.py
scripts/live_valentini.py
scripts/backfill_parquet.py
scripts/backfill_futures_parquet.py
scripts/download_nifty_universe.py
```

**Files:**
- Delete: the 15 files listed above (plus their `scripts/__pycache__/*.pyc` entries if present).

**Interfaces:**
- Consumes: nothing in active flow.
- Produces: no active symbols removed.

- [ ] **Step 1: Confirm no external importer (re-verify)**

Run from repo root:
```bash
grep -rlnE "from scripts|import sweep|import backtest_|import cost_breakdown|import ema_cross|import benchmark|import live_runner_run|import dev_serve|import apply_graph|_sweep_common" --include=*.py . | grep -v '^scripts/'
```
Expected: no output (no production/test code references these).

- [ ] **Step 2: Delete the 15 research scripts**

```bash
cd /Users/apple/Downloads/nTrade
rm -f scripts/_sweep_common.py scripts/sweep_morning_vah_val.py scripts/sweep_morning_vah_val2.py \
      scripts/sweep_morning_vah_val3.py scripts/sweep_chandelier_trail.py scripts/analyze_sweep2.py \
      scripts/backtest_morning_vah_val.py scripts/backtest_valentini.py scripts/cost_breakdown.py \
      scripts/benchmark_fetch.py scripts/benchmark_latency.py scripts/ema_cross_run.py \
      scripts/live_runner_run.py scripts/dev_serve_api.py scripts/apply_graph_resolutions.py
find scripts -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
```

- [ ] **Step 3: Verify active scripts + core still import**

Run:
```bash
.venv/bin/python -c "import ntrade, api.server; print('core OK')"
ls scripts/   # confirm 9 KEEP files present
```
Expected: `core OK`; `scripts/` contains exactly the 9 KEEP files.

- [ ] **Step 4: Run full test suite (smoke)**

Run:
```bash
.venv/bin/python -m pytest -q 2>&1 | tail -20
```
Expected: no collection errors; the previously-fixed `test_orb_rvol_screener.py` still passes; no import errors from removed scripts (none were imported by tests).

- [ ] **Step 5: Commit**

```bash
git add -A scripts/
git commit -m "cleanup: delete self-contained research/one-off scripts (keep active entry points)"
```

---

## Self-Review

1. **Spec coverage:** Tier B deeper cleanup = shim (Task 1) + research scripts (Task 2). `valentini.ts` excluded with rationale (tested + debt-ledger-tracked). Active entry points retained. ✓
2. **Placeholder scan:** No TBD/TODO; each step has exact commands and expected output. ✓
3. **Type consistency:** Imports in Task 1 reference existing `ntrade.domain.market_hours` symbols (verified present). Task 2 deletions are whole-file removals; no cross-task symbol dependencies. ✓
4. **Irreversibility check:** Task 2 deletes files from a git repo — recoverable via `git`. Active execution paths verified retained. The user explicitly approved this deeper cleanup. ✓
