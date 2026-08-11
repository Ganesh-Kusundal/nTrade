# Task 4 Brief: Volume-confirmed accumulation

## Where this fits

Project: nTrade — a Valentini AMT scalper on an event-bus kernel. Tasks 1-3
added `accum_volume_mult=1.5` and the live `_step` / `_leg_start_idx`. This
task gates the `absorbing -> accumulating` phase transition on recent volume so
a dead drift back to the POC cannot confirm accumulation.

## Design note (verbatim from the plan, reconciled)

The volume baseline is the **prior MEDIAN** bar volume (not the mean): the
absorption spike (e.g. 1500 vs 100 baseline) would inflate the mean and reject
normal follow-through volume, breaking the existing strategy suite. The
architecture sentence of the plan already says "prior median volume"; the mean
was a typo in the code block.

## Requirements (verbatim from the plan)

**Files:**
- Modify: `ntrade/engines/strategies.py` (accumulation check in `_update_phase`)
- Modify: `tests/test_valentini_leg_anchor.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_valentini_leg_anchor.py`:

```python
# ------------------------------------------------------------------ volume accumulation

def test_accumulation_requires_recent_volume(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)                       # base 100-volume bars
    _absorption_bar(k, 30, at=110.0, volume=1500)  # absorbing at VAL
    assert strat.phase == "absorbing"
    # Bars 31,32 drift back to the POC on LOW volume (well below 1.5x the
    # prior median 100): accumulation must NOT confirm on a dead retrace.
    _candle(k, 31, close=118.0, volume=30)
    _candle(k, 32, close=118.0, volume=30)
    assert strat.phase == "absorbing"
    # A high-volume test of the POC confirms the move -> accumulating.
    _candle(k, 33, close=118.0, volume=300)
    assert strat.phase == "accumulating"
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_accumulation_requires_recent_volume -q`
Expected: FAIL — after bars 31/32, phase is `"accumulating"` (price-only check passes today).

**Step 3: Gate accumulation on recent volume**

Replace the accumulation check in `_update_phase` (currently the block that
reads `if (elapsed >= 2 and abs(close - poc) <= 2 * step):`). Note: `frame` is
not currently a local in `_update_phase` — add `frame = pd.DataFrame(self._rows)`
at the top of `_update_phase` (after `close = float(event.close)`).

```python
            recent_vol = float(frame["volume"].iloc[-2:].sum())
            prior_vol = frame["volume"].iloc[:-2]
            avg_vol = float(prior_vol.median()) if len(prior_vol) else 0.0
            # Volume confirmation: the move back to the POC must carry real
            # participation (guide §4.1), not a dead drift. NaN/empty guard:
            # no prior history means no volume test to fail. Median baseline
            # (not mean) — the absorption spike would otherwise inflate the
            # average and reject normal follow-through volume.
            vol_ok = (avg_vol <= 0
                      or recent_vol >= self.accum_volume_mult * avg_vol)
            if (elapsed >= 2 and abs(close - poc) <= 2 * step and vol_ok):
                self._phase = "accumulating"
```

**Step 4: Run the new test to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: PASS — 3 passed.

**Step 5: Verify zero-parity still passes**

Run: `python -m pytest tests/test_zero_parity_across_modes.py -q`
Expected: `3 passed`. In the synthetic frame, bars 30-31 volume is 3000+1000=4000 and prior median ≈1000 → 4000 ≥ 1.5×1000 → accumulation still confirms; entry at `111.0` unchanged.

**Step 6: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: volume-confirmed valentini accumulation"
```

## Global Constraints (apply to this task)

- Only modify `ntrade/engines/strategies.py` and `tests/test_valentini_leg_anchor.py`.
- `tests/test_valentini_strategy.py` has uncommitted WIP that must NOT be
  staged or committed. Do NOT touch that file, and do NOT import from it.
- Existing constructor params must keep their defaults and semantics.
- Keep it lazy: no new modules, no refactors of unrelated code.
- Use `prior_vol.median()` — NOT `mean()`.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-4-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the pytest output lines (leg-anchor run + zero-parity run), and any concerns.
