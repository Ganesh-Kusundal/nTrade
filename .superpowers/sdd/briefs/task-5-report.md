# Task Group 5 — D-017: gate.py equity derives from the portfolio read model

**Status:** DONE_WITH_CONCERNS · Commit `8cbf69a` (branch `g4-parity-complexity-batch`)

## What changed

### `ntrade/runner/gate.py`
- Rewrote `_equity_trace` to consume the canonical portfolio read-model stream instead of re-deriving money state from raw `OrderFilledEvent` + `TickEvent`/`QuoteEvent`:
  - Starts `cash = float(initial_cash)`; keeps a per-symbol `(quantity, ltp)` map.
  - On `PositionUpdatedEvent`: upsert `(quantity, ltp)`; `quantity == 0` drops the symbol.
  - On `BalanceChangedEvent`: `cash = event.balance`.
  - `eq = cash + sum(q * ltp ...)`; tracks `peak`; yields `(peak, eq)` after each state change (non-portfolio events are skipped via `continue`).
- Removed the fill-by-fill cash math and the `TickEvent`/`QuoteEvent` LTP mark; dropped the now-unused `QuoteEvent`/`TickEvent` imports.
- `build_paper_report` untouched: still reads `OrderFilledEvent` for `fills`/per-fill charges/`total_charges`, and `final_equity` still comes from `ctx.account.balance` + portfolio positions (which matches `RiskEngine.equity`). Report structure unchanged.

### `tests/test_paper_gate.py`
- Added `test_equity_trace_converges_on_portfolio_read_model`: after a one-BUY-fill (10 @ 100.0) replay run, asserts `report["final_equity"] == pytest.approx(k.risk_engine.equity(), abs=0.01)` and that the trace's final point `_equity_trace(...)` converges on `k.risk_engine.equity()`.

## TDD sequence

1. **Wrote the test first.** Ran `./.venv/bin/python -m pytest tests/test_paper_gate.py -q` → **1 failed, 2 passed**: `AssertionError: assert 98999.9438 == 99999.9438 ± 0.01` (old trace dropped the position's ₹1,000 mark because `ltp` was empty).
2. **Implemented** the `_equity_trace` rewrite. Gate tests → **3 passed**.
3. **Full suite.**

## Exact commands and output tails

```
$ ./.venv/bin/python -m pytest tests/test_paper_gate.py -q        # pre-impl (red)
...
E       assert 98999.9438 == 99999.9438 ± 0.01
FAILED tests/test_paper_gate.py::test_equity_trace_converges_on_portfolio_read_model
1 failed, 2 passed in 0.27s

$ ./.venv/bin/python -m pytest tests/test_paper_gate.py -q        # post-impl (green)
...                                                              [100%]
3 passed in 0.24s

$ ./.venv/bin/python -m pytest -q                                 # full suite
...
623 passed in 5.71s
```

## Commit

- `8cbf69a` — `gate equity derives from portfolio read-model events (D-017)`
  - Files staged explicitly (no `git add -A`): `ntrade/runner/gate.py`, `tests/test_paper_gate.py`.
  - Pre-existing uncommitted changes (`ntrade/brokers/dhan*.py`, scratch files, `.kanban/`, `.superpowers/` briefs/reports) were **not** touched or staged.

## Concerns for review

1. **Intermediate-state peak (conservative drawdown).** `PortfolioEngine.on_filled` debits `account.balance` *before* publishing, then publishes `PositionUpdatedEvent` → `BalanceChangedEvent`. A strict in-history replay therefore computes one intermediate point — after `PositionUpdatedEvent` but before `BalanceChangedEvent` — where the new position is counted against the *pre-fill* cash (e.g. eq spikes to ₹1,01,000 for a 10×₹100 BUY, then settles at ₹99,999.94). `RiskEngine._update_breakers` never observes that state (equity is only sampled between complete event batches), so the gate's `max_drawdown_pct` (≈0.99% in the one-fill case) is *higher* than RiskEngine's own view (≈0.00%: peak stays at initial cash). This is the letter of the brief's prescribed event order and is a conservative (fail-safe) upper bound for a validation gate, but it is not exact RiskEngine parity on `max_drawdown_pct`. If exact parity is required, `_equity_trace` would need to pair each `PositionUpdatedEvent` with its following `BalanceChangedEvent` and apply the cash first (treat the two as one atomic state change). The `final_equity` invariant and the trace's final point both match `RiskEngine.equity()` exactly (to 2dp), which was the brief's asserted acceptance criterion.
2. **Empty-run path.** With no `PositionUpdatedEvent`/`BalanceChangedEvent` in history the trace yields nothing (history is empty), so `max_drawdown_pct = 0.0` and `final_equity = 100_000.0` — existing test stays green.

---

## Review fix round 1

**Reviewer concern (correctness bug):** `PortfolioEngine.on_filled` debits cash *before* publishing, then publishes `PositionUpdatedEvent` → `BalanceChangedEvent`, both already post-fill. The strict in-history replay applied the position update against pre-fill cash → spurious intermediate equity spike (10×₹100 BUY → eq 1,01,000). `final_equity` converged, but `peak`/`max_drawdown_pct` diverged from `RiskEngine.equity` — the exact MTM divergence the finding set out to eliminate.

**What changed — `ntrade/runner/gate.py::_equity_trace`:**
- Yields the initial `(initial_cash, initial_cash)` point before the loop.
- `PositionUpdatedEvent` only updates the per-symbol `(quantity, ltp)` map — no equity computation, no yield.
- `BalanceChangedEvent` sets `cash = event.balance`, THEN computes `eq = cash + Σ q·ltp`, tracks `peak`, and yields `(peak, eq)`.
- Every yielded point is therefore a settled (post-fill) state, exactly what `RiskEngine.equity()` (risk_engine.py:44-47) returns after that event group.
- `PositionSyncEngine` edge case (position-only sync with no `BalanceChangedEvent`): position events update the map; the next balance event reflects them. Acceptable per brief, no over-engineering.

**Test (`tests/test_paper_gate.py::test_equity_trace_converges_on_portfolio_read_model`):** strengthened to catch the spike — after the single BUY at 100.0, asserts the trace's `peak == 100_000.0` (no intermediate equity above the settled value), `final_eq` and `report["final_equity"]` equal `k.risk_engine.equity()` (±0.01), and `max_drawdown_pct == pytest.approx(0.0, abs=0.01)`. (Note: the review's literal "peak equals final_equity" cannot hold with the required initial `(initial_cash, initial_cash)` yield — the initial point sits above the post-charge settled value — so the equivalent no-spike assertion is `peak == initial_cash` + zero drawdown.)

**TDD sequence:**

```
$ ./.venv/bin/python -m pytest tests/test_paper_gate.py -q        # red
>       assert peak == pytest.approx(100_000.0, abs=0.01)
E       assert 101000.0 == 100000.0 ± 0.01
FAILED tests/test_paper_gate.py::test_equity_trace_converges_on_portfolio_read_model
1 failed, 2 passed in 0.26s

$ ./.venv/bin/python -m pytest tests/test_paper_gate.py -q        # green
...                                                                 [100%]
3 passed in 0.23s

$ ./.venv/bin/python -m pytest -q                                 # full suite
...
623 passed in 5.68s
```

**Commit:** `2c2d4e3` — `D-017 gate equity: settle at balance events (no pre-fill spike)`
- Staged explicitly (no `git add -A`): `ntrade/runner/gate.py`, `tests/test_paper_gate.py` only.
- Pre-existing uncommitted changes (`ntrade/brokers/dhan*.py`, `.kanban/`, `.superpowers/` briefs) left untouched.
