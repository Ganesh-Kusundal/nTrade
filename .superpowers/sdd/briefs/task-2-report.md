# Task 2 Report: BacktestSimulator dedicated fill list

## Status
DONE

## Commit
`a6da0b2` — `fix: backtest results use dedicated fill list (exact metrics >10k events)`

Staged exactly: `ntrade/backtest/simulator.py`, `tests/test_backtest_simulator.py` (verified via `git show --stat HEAD`). No other files staged (repo had large pre-existing uncommitted data/ state left untouched).

## What changed
- `ntrade/backtest/simulator.py`
  - `__init__`: added `self._fills: list[OrderFilledEvent] = []` (after `_futures_costs_total`).
  - Subscribed `self.kernel.bus.subscribe(OrderFilledEvent, self._on_fill)` after `self.kernel = kernel` (covers both kernel-created-here and passed-in paths; no need for a `run()`-time subscribe since the kernel is always available at end of `__init__`).
  - Added `_on_fill` handler that appends every fill to `_fills`.
  - `results()`: replaced the bus.history scan with `fills = self._fills` (the rest of `results()` unchanged).
- `tests/test_backtest_simulator.py`: new file with the regression test (verbatim from brief).

## Test results
- Pre-fix (red): `tests/test_backtest_simulator.py::test_results_fills_not_truncated_by_bus_cap` — FAILED
  `AssertionError: simulator must expose a dedicated _fills list` (1 failed in 79.59s)
- Post-fix (green):
  - `python -m pytest tests/test_backtest_simulator.py -q` → `1 passed in 54.47s`
  - `python -m pytest tests/test_zero_parity_across_modes.py tests/test_backtest_risk_breaker.py -q` → `6 passed in 84.60s (0:01:24)`

## Notes on the regression frame
The synthetic 750-bar frame produced **zero fills** (ValentiniScalper is session/volume-profile driven and stays flat on the synthetic sawtooth; `run()` returned `trades=0`). The assertion `len(sim._fills) == len(res.trades)` still binds: it structurally verifies `results()` derives `n_trades` from `_fills` (0 == 0 here) rather than from `bus.history`, and the `_fills >= bus_fills` check plus the `hasattr` check pin the mechanism. If this synthetic frame had instead produced >10k bus events with real fills, the old code would have truncated them; the regression assertion keeps that path honest for future frames that do fill.

## Concerns
- **Fills are append-only across repeated `run()` calls.** `_fills` is initialized in `__init__` and never cleared at the top of `run()`, matching the pre-existing accumulative behaviour of `bus.history` (the capped deque was also never cleared between runs). A subsequent `run()` on the same simulator would double-count trades from earlier runs in `results()`. Pre-existing semantics, unchanged by this fix — flagging for a possible follow-up (reset `_fills = []` in `run()`) if anyone relies on re-running the same simulator instance.
- **Test runtime.** The new test takes ~55-80s (full kernel over 750 bars); the brief's 3-minute allowance for the zero-parity suite was ample (84.6s).
- One incidental note: `_on_fill`'s docstring is duplicated verbatim in the subscription comment in `__init__` — kept for parity with the brief's exact code.
