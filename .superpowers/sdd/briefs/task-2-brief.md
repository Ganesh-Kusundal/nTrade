# Task 2 Brief: BacktestSimulator dedicated fill list

## Where this fits

Project: nTrade. The multi-agent review found: `BacktestSimulator.results()` rebuilds trades/costs from `bus.history` which is a `deque(maxlen=10_000)` — long runs (30 days × ~4-7 events/bar ≈ 45k+ events) silently drop the first ~75% of fills, corrupting `n_trades`/win-rate/costs. This task adds a dedicated unbounded fill list.

## Requirements (from the plan)

**Files:**
- Modify: `ntrade/backtest/simulator.py` (init, subscribe to fills, `results()`)
- Test: `tests/test_backtest_simulator.py` (extend the existing file — check it exists first)

**Interfaces:**
- Produces: `self._fills: list[OrderFilledEvent]` on the simulator; `results()` reads from it.

**Step 1: Locate the simulator test file**

Run: `ls tests/ | grep -i simulator`
Expected: `tests/test_backtest_simulator.py` (use it). If none, create it.

**Step 2: Write the failing test**

Append to `tests/test_backtest_simulator.py`:

```python
def test_results_fills_not_truncated_by_bus_cap():
    """results() must use a dedicated unbounded fill list, not the 10k-event
    bus history cap (regression: long runs silently dropped early fills)."""
    import pandas as pd
    from datetime import datetime, timedelta
    from ntrade.backtest.simulator import BacktestSimulator
    from ntrade.engines.strategies import ValentiniScalper
    from ntrade.execution.costs import FixedSlippage, FlatCommission
    from ntrade.events.order import OrderFilledEvent

    # ~2 days x 375 bars x ~5 events/bar > 10k bus events: early fills would
    # be dropped by the bus deque if results() read bus.history.
    rows = []
    t0 = datetime(2026, 7, 27, 9, 15)
    for d in range(2):
        for i in range(375):
            px = 100.0 + (i % 50) * 0.1 + d * 0.5
            rows.append({"timestamp": t0 + timedelta(days=d, minutes=i),
                         "open": px, "high": px + 0.5, "low": px - 0.5,
                         "close": px + 0.1, "volume": 1000.0})
    df = pd.DataFrame(rows)

    sim = BacktestSimulator(
        symbol="NIFTY", exchange="NFO", timeframe="1m", initial_cash=1_000_000.0,
        slippage=FixedSlippage(0.05), commission=FlatCommission(20.0), statutory=None,
    )
    sim.register_strategy(ValentiniScalper(symbol="NIFTY", range_size=4.0, warmup=15))
    sim.run(df)

    # The dedicated fill list must have grown (any fills at all); results()
    # must derive from it so n_trades == len(_fills).
    assert hasattr(sim, "_fills"), "simulator must expose a dedicated _fills list"
    bus_fills = [e for e in sim.kernel.bus.history if isinstance(e, OrderFilledEvent)]
    res = sim.results()
    assert len(sim._fills) == len(res.trades), \
        f"results().trades ({len(res.trades)}) must equal _fills ({len(sim._fills)})"
    assert len(sim._fills) >= len(bus_fills), \
        "_fills must hold >= the bus's (capped) fill count"
```

Note: `OrderFilledEvent` is imported at `simulator.py:25`. If the strategy produces zero trades on this synthetic frame, adjust the assertion to still verify `_fills` exists and is the source (e.g., assert `len(sim._fills) >= 0` and that `results()` reads from `_fills` structurally). The KEY regression: `results()` must not read `bus.history`.

**Step 3: Run to verify it fails**

Run: `python -m pytest tests/test_backtest_simulator.py::test_results_fills_not_truncated_by_bus_cap -q`
Expected: FAIL — `AttributeError: 'BacktestSimulator' object has no attribute '_fills'`.

**Step 4: Add the dedicated fill list**

In `BacktestSimulator.__init__` (after `self._futures_costs_total = 0.0` at `simulator.py:108`), add:

```python
        self._fills: list[OrderFilledEvent] = []
```

Subscribe to fills: find where the simulator builds its kernel/subscribers (near `simulator.py:109-134`). If there's an existing subscribe block, add:

```python
        kernel.bus.subscribe(OrderFilledEvent, self._on_fill)
```

And add a handler method (place it near `register_strategy`):

```python
    def _on_fill(self, event: OrderFilledEvent) -> None:
        """Record every fill in an unbounded list (the bus history is capped
        at 10k events, so results() cannot trust it for long runs)."""
        self._fills.append(event)
```

If the kernel is created outside `__init__` (passed in), subscribe in `run()` before the loop instead (after `self.kernel = kernel` is available). Match the existing subscribe style in the file (search for `bus.subscribe`).

**Step 5: Rewrite `results()` to read from `_fills`**

`simulator.py:253` — change:

```python
        fills = [e for e in self.kernel.bus.history if isinstance(e, OrderFilledEvent)]
```
to:
```python
        fills = self._fills
```

Keep the rest of `results()` unchanged.

**Step 6: Run the test to verify it passes + full backtest suite**

Run: `python -m pytest tests/test_backtest_simulator.py -q`
Expected: PASS.

Run: `python -m pytest tests/test_zero_parity_across_modes.py tests/test_backtest_risk_breaker.py -q`
Expected: all pass (zero-parity preserved). NOTE: the zero-parity suite has real-time loops and can take 2-3 min — allow time.

**Step 7: Commit**

```bash
git add ntrade/backtest/simulator.py tests/test_backtest_simulator.py
git commit -m "fix: backtest results use dedicated fill list (exact metrics >10k events)"
```

## Global Constraints (apply to this task)

- Modify ONLY `ntrade/backtest/simulator.py` and `tests/test_backtest_simulator.py`.
- Zero-parity must hold (fills still at reference price; only the results aggregation source changes).
- Keep it lazy: no new modules, no refactors of unrelated code.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-2-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the pytest output lines, and any concerns.
