# Task 3 Brief: Leg-anchored volume profile

## Where this fits

Project: nTrade — a Valentini AMT scalper on an event-bus kernel. Tasks 1-2
added `_leg_start_idx` (inert) and live `_step`. This task makes the location
volume profile rebuild per impulse leg instead of per session, using 1m candle
span (NOT range-bar span — see design note below).

## Design note (verbatim from the plan)

A range bar closes the moment its span reaches `range_size` (`range_bars.py:97`),
so a completed range bar's span is always ≈`range_size` — it can never reach
`2 × range_size`. An impulse must therefore be detected on **1m candle span**,
not range-bar span.

## Requirements (verbatim from the plan)

**Files:**
- Modify: `ntrade/engines/strategies.py:271-276` (session reset), `:289-290` (profile build)
- Modify: `tests/test_valentini_leg_anchor.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_valentini_leg_anchor.py`:

```python
# ------------------------------------------------------------------ leg-anchored profile

def _impulse_bar(k, i, at, span=10.0, volume=2000):
    """A single 1m candle whose high-low range >= leg_impulse_mult * range_size
    (2.0 * 4.0 = 8.0) — a directional impulse that starts a new leg."""
    _candle(k, i, close=at, open_=at - span / 2,
            high=at + span / 2, low=at - span / 2, volume=volume)


def test_impulse_candle_reanchors_leg():
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _uptrend_bars(k, 30)                 # leg starts at row 0 (no impulse yet)
    assert strat._leg_start_idx == 0
    _impulse_bar(k, 30, at=115.0)        # span 10 >= 8 -> new leg at row 30
    assert strat._leg_start_idx == 30
    _candle(k, 31, close=116.0)          # a quiet bar keeps the same leg
    assert strat._leg_start_idx == 30
    _impulse_bar(k, 32, at=118.0)        # another impulse -> leg re-anchors
    assert strat._leg_start_idx == 32
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_impulse_candle_reanchors_leg -q`
Expected: FAIL — `strat._leg_start_idx` is `0` (attr exists from Task 1, never updated).

**Step 3: Reset leg index on session change**

At the new-day block in `on_candle_closed` (`strategies.py:271-276`), inside the
`if key != self._session_key:` branch after `self._profile = None`, add:

```python
            self._leg_start_idx = 0
```

**Step 4: Detect the impulse leg and build the profile on the leg slice**

Replace the profile build at `strategies.py:289-290` (note: line numbers may
have shifted +8 from Task 2's insertion; locate it by the comment `Guide
"location": POC/VAH/VAL over TODAY's rows`):

```python
        # Guide "location": POC/VAH/VAL over the CURRENT LEG, not the whole
        # session. A new impulse leg starts at the last 1m candle whose span
        # >= leg_impulse_mult * range_size. Range-bar spans can't drive this
        # (they close at range_size by construction), so the impulse test is
        # on 1m candle span. ponytail: single-candle span heuristic; a real
        # leg detector (multi-candle momentum) would be over-engineering here.
        if not frame.empty:
            leg_frame = frame.iloc[self._leg_start_idx:]
            if self._leg_start_idx > 0 and not leg_frame.empty:
                self._profile = build_volume_profile(
                    leg_frame, step=self._step)
            else:
                self._profile = build_volume_profile(
                    frame, step=self._step)
```

**Step 5: Advance the leg index on each new impulse**

In `on_candle_closed`, after `self._range_bars = build_range_bars(...)` and
before the profile build, add the leg scan. It reads 1m candle spans from
`frame`:

```python
        # Advance the leg anchor: the last 1m candle with span >= impulse
        # threshold starts a fresh leg. Clamp to the windowed frame.
        imp_thr = self.leg_impulse_mult * self._step
        imp_idx = -1
        for i in range(len(frame) - 1, -1, -1):
            row = frame.iloc[i]
            if (float(row["high"]) - float(row["low"])) >= imp_thr:
                imp_idx = i
                break
        if imp_idx >= 0:
            self._leg_start_idx = imp_idx
        self._leg_start_idx = min(self._leg_start_idx, len(frame) - 1)
```

**Step 6: Run the new test to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: PASS — 2 passed (ATR + leg anchor).

**Step 7: Run zero-parity to confirm no regression**

Run: `python -m pytest tests/test_zero_parity_across_modes.py -q`
Expected: `3 passed`. The synthetic frame's absorption bar (bar 30) has span
0.1 < 8, so no impulse is detected and the profile stays session-wide — entry
at `111.0` preserved.

**Step 8: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: leg-anchored volume profile for valentini location"
```

## Global Constraints (apply to this task)

- Only modify `ntrade/engines/strategies.py` and `tests/test_valentini_leg_anchor.py`.
- `tests/test_valentini_strategy.py` has uncommitted WIP that must NOT be
  staged or committed. Do NOT touch that file, and do NOT import from it.
- Existing constructor params must keep their defaults and semantics.
- Keep it lazy: no new modules, no refactors of unrelated code.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-3-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the pytest output lines (leg-anchor run + zero-parity run), and any concerns.
