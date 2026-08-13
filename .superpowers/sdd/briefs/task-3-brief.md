# Task 3 Brief: Divergence-exit per-bar benchmark (Python + TS mirror)

## Where this fits

Project: nTrade. The multi-agent review found the divergence exit compares ONE range-bar's volume to the WHOLE entry-leg's SUM × 0.6 (`strategies.py:648` vs `:328`) — every higher-high range bar looks "weak", so the auction-following runner exits after 1-2 bars. Fix: benchmark = the leg's per-bar MEAN volume. Port to both Python and the TS mirror (parity).

## Requirements (from the plan)

**Files:**
- Modify: `ntrade/engines/strategies.py` (line 648), `ui/src/lib/valentini.ts` (line 536)
- Test: `tests/test_valentini_leg_anchor.py`, `ui/src/lib/__tests__/valentini.test.ts`

**Interfaces:**
- Produces: `act["impulse_volume"]` = the leg's per-bar MEAN volume (not sum). Consumed by `_divergence_exit` (`strategies.py:328`).

**Step 1: Write the failing test (Python)**

Append to `tests/test_valentini_leg_anchor.py`:

```python
# ------------------------------------------------------------------ divergence benchmark

def test_divergence_not_fired_on_normal_volume_followthrough(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0, volume=1000)
    _candle(k, 32, close=122.0, volume=1000)     # BUY entry, ~100-vol leg bars
    # A higher-high range bar at NORMAL volume must NOT trigger divergence
    # (the old leg-SUM benchmark would see 100 << 0.6 * leg_sum and exit).
    monkeypatch.setattr(
        "ntrade.engines.strategies.build_range_bars",
        lambda *a, **kw: pd.DataFrame({
            "high": [116.0, 120.0, 124.0, 127.0],
            "low":  [114.0, 117.0, 121.0, 122.0],
            "close":[115.5, 119.0, 123.0, 126.0],
            "volume":[100.0, 100.0, 100.0, 100.0],
            "is_complete":[True, True, True, True],
        }),
    )
    _candle(k, 33, close=128.0, open_=126.5, high=129.0, low=126.0, volume=100)
    sells = [f for f in _fills(k) if f.side == "SELL"]
    assert not sells, f"normal-volume followthrough must NOT exit via divergence, got {len(sells)} sells"
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_divergence_not_fired_on_normal_volume_followthrough -q`
Expected: FAIL — a divergence SELL fires (leg-sum benchmark).

**Step 3: Fix the benchmark in `_emit_entry`**

`strategies.py:648` — change:

```python
        impulse_volume = float(leg["volume"].sum()) if not leg.empty else 0.0
```
to:
```python
        impulse_volume = float(leg["volume"].mean()) if not leg.empty else 0.0
```

**Step 4: Run the Python test to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: all pass (the new test + existing).

**Step 5: Port to the TS mirror**

`ui/src/lib/valentini.ts:536` — change:

```ts
          const impulseVolume = dayBars.slice(legStartIdx).reduce((a, b) => a + (b.volume || 0), 0)
```
to:
```ts
          const leg = dayBars.slice(legStartIdx)
          const impulseVolume = leg.length > 0
            ? leg.reduce((a, b) => a + (b.volume || 0), 0) / leg.length
            : 0
```

**Step 6: Update the TS divergence-test comments (thresholds)**

`ui/src/lib/__tests__/valentini.test.ts` has two comments referencing the SUM benchmark. The runner fixture's leg is ~20 bars → mean ≈ 11100/20 = 555; divergence fires when bar volume < 0.6 × 555 = 333. Update:
- Line ~375-376 comment: `impulseVolume at entry ~11100, so divergence fires when bar volume < 0.6 x 11100 = 6660` → `impulseVolume at entry ~555 (mean), so divergence fires when bar volume < 0.6 x 555 = 333`
- Line ~354 comment: `Volume is HIGH (8000 > 0.6 x impulseVolume ~6660)` → `(8000 > 0.6 x ~555 = 333)`

The fixture volumes themselves (80 weak, 8000 strong) still satisfy the new thresholds — no fixture change needed, only the comments.

Run: `cd ui && npm test 2>&1 | tail -4` — all pass.

**Step 7: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py ui/src/lib/valentini.ts ui/src/lib/__tests__/valentini.test.ts
git commit -m "fix: divergence exit per-bar benchmark (python + ts mirror)"
```

## Global Constraints (apply to this task)

- Modify ONLY those 4 files.
- Parity: BOTH Python and TS must use the per-bar mean (identical semantics).
- Keep it lazy: no new modules, no refactors of unrelated code.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-3-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the pytest + vitest output lines, and any concerns.
