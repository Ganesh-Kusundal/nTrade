# Task 1 Brief (T-020) — Remove fake streaming no-ops from the capability surface

This is the full requirements text for Task 1 of the integration-completeness batch. Follow it verbatim, in order, with TDD (failing test first).

**Goal (batch):** Wire the 10 planned-but-unintegrated subsystems into the live path. Task 1 is the smallest, cleanest removal — it deletes two fake streaming methods from `DhanBroker`.

## Global Constraints (bind every task)

- Test command is always `./.venv/bin/python -m pytest -q` (baseline: **624 passing**, run from `/Users/apple/Downloads/nTrade`).
- `rg` is not installed in this shell — use `grep -rn` if a search is needed.
- Events stay `@dataclass(frozen=True, kw_only=True)` extending `ntrade.events.base.Event`; `ts` comes from the kernel clock, never `datetime.now()`.
- Domain layer stays broker-agnostic; no new third-party dependencies.
- Commit message style matches `git log --oneline` (short `<ID> subject`, e.g. `T-020 remove fake streaming no-op capabilities`).
- The working tree carries 25 uncommitted files from a prior batch — stage ONLY the files listed in this task's `Files:` section.

## Task 1: Remove fake streaming no-ops from the capability surface (T-020)

**Files:**
- Modify: `ntrade/brokers/dhan.py:883-898` (delete `_market_feed` + `_order_update_stream`)
- Test: `tests/test_dhan_broker.py`

**Context:** `_market_feed` and `_order_update_stream` (dhan.py:883-898) call `BrokerAdapter.subscribe()` — which only sets a flag, no websocket/data path exists — then return `instrument.stream`. They fake streaming. The real streaming path is `sources/dhan_feed.py`. They have zero callers repo-wide (grep of `market_feed`/`order_update_stream` only hits dhan.py, capabilities.py, and `sources/market_feed.py`, which is an unrelated module). Completes kanban T-020.

### Step 1: Write the failing test

Append to `tests/test_dhan_broker.py`:

```python
def test_no_fake_streaming_capabilities():
    import ntrade.brokers.dhan as dhan_mod
    from ntrade.brokers.capabilities import registered_capabilities

    assert not hasattr(dhan_mod, "_market_feed")
    assert not hasattr(dhan_mod, "_order_update_stream")
    caps = registered_capabilities()
    assert "market_feed" not in caps
    assert "order_update_stream" not in caps
```

Note: `_market_feed`/`_order_update_stream` are module-level `@capability` functions (registered in `_CAPABILITIES`), not `DhanBroker` methods — assert against the module and the capability registry, not `hasattr(DhanBroker, ...)` (that would be vacuous).

### Step 2: Run test to verify it fails

Run: `./.venv/bin/python -m pytest tests/test_dhan_broker.py::test_no_fake_streaming_capabilities -q`
Expected: FAIL (both `hasattr` are True today).

### Step 3: Delete the two methods

In `ntrade/brokers/dhan.py`, delete the `_market_feed` method and the `_order_update_stream` method (dhan.py:883-898) and their `@capability(...)` decorators. Also drop any `subscribe`/`LiveStream` imports that only these two used, if they become unreferenced (`grep -rn "subscribe" ntrade/brokers/dhan.py` — keep if the base class still needs it).

### Step 4: Run test to verify it passes

Run: `./.venv/bin/python -m pytest tests/test_dhan_broker.py::test_no_fake_streaming_capabilities -q`
Expected: PASS.

### Step 5: Full suite + commit

Run: `./.venv/bin/python -m pytest -q` → 624+ passing.

```bash
git add ntrade/brokers/dhan.py tests/test_dhan_broker.py
git commit -m "T-020 remove fake streaming no-op capabilities"
```

## Report contract

After completing, write a report to `.superpowers/sdd/briefs/task-1-report.md` containing: what you changed, the exact test command(s) run and their output (pass/fail counts), the commit hash, and any concerns. Return to the controller ONLY: status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash, a one-line test summary, and any concerns. Do not paste the full report into the return message.
