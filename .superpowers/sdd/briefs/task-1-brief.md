# Task 1 Brief: Constructor params + state for the three phases

## Where this fits

Project: nTrade — a Valentini AMT scalper built on an event-bus kernel. This is
the first task of the "Valentini AMT Corrections" batch. Tasks 2-4 will use the
state added here.

## Requirements (verbatim from the plan)

**Files:**
- Modify: `ntrade/engines/strategies.py:107-175` (constructor)

**Interfaces:**
- Produces: instance attrs `self.leg_impulse_mult: float`,
  `self.accum_volume_mult: float`, `self._atr: float`, `self._step: float`,
  `self._leg_start_idx: int` — used by Tasks 2-4.

**Step 1: Add the two constructor params**

In `ValentiniScalper.__init__` (`ntrade/engines/strategies.py:107-118`), add two
params after `cvd_confirm_bars: int = 3`:

```python
                 depth_imbalance_min: float | None = None,
                 require_cvd: bool = True, cvd_confirm_bars: int = 3,
                 leg_impulse_mult: float = 2.0,
                 accum_volume_mult: float = 1.5):
```

**Step 2: Assign the params**

In the constructor body, after `self.cvd_confirm_bars = max(1, int(cvd_confirm_bars))` (`strategies.py:155`), add:

```python
        self.leg_impulse_mult = max(1.0, float(leg_impulse_mult))
        self.accum_volume_mult = max(0.0, float(accum_volume_mult))
```

**Step 3: Add the new state attrs**

In the internal-state block (after `self._pending_age: int = 0` at `strategies.py:175`), add:

```python
        self._atr: float = 0.0            # current ATR, recomputed per candle
        self._step: float = 1.0           # step = max(range_size, ATR) — ATR floor
        self._leg_start_idx: int = 0      # row index where the current impulse leg began
```

**Step 4: Run the existing strategy tests to confirm no regression**

Run: `python -m pytest tests/test_valentini_strategy.py -q`
Expected: `24 passed` (new attrs are inert until Tasks 2-4 use them).

**Step 5: Commit**

```bash
git add ntrade/engines/strategies.py
git commit -m "feat: valentini leg-anchor/ATR/volume knobs and state"
```

## Global Constraints (apply to this task)

- Only modify `ntrade/engines/strategies.py`. No other source file changes.
- `tests/test_valentini_strategy.py` has uncommitted WIP that must NOT be
  staged or committed. Do NOT touch that file.
- Existing constructor params must keep their defaults and semantics
  (`range_size`, `atr_period`, `warmup`, `abs_lookback`, etc.).
- Keep it lazy: no new modules, no refactors of unrelated code.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-1-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the pytest output line, and any concerns.
