# Task 4 Report: Volume-confirmed accumulation

## Status: DONE

## Commit

`cf88dd7d41c72c057be540ff2fa10b680d7c6856` — `feat: volume-confirmed valentini accumulation`

Exactly two files in the commit:
- `ntrade/engines/strategies.py` (+13/-1)
- `tests/test_valentini_leg_anchor.py` (+20)

`tests/test_valentini_strategy.py` was NOT touched and remains unstaged with its pre-existing uncommitted WIP intact.

## Test results

Step 2 (new test, pre-fix, expected FAIL):
```
FAILED tests/test_valentini_leg_anchor.py::test_accumulation_requires_recent_volume
AssertionError: assert 'accumulating' == 'absorbing'
1 failed in 0.86s
```

Step 4 (leg-anchor suite, post-fix):
```
... [100%]
3 passed in 1.51s
```

Step 5 (zero-parity, ~84s):
```
... [100%]
3 passed in 83.80s (0:01:23)
```

Step 6 (strategy suite, median baseline keeps these green):
```
........................ [100%]
24 passed in 5.94s
```

## Changes made

`ntrade/engines/strategies.py` `_update_phase`:
- Added `frame = pd.DataFrame(self._rows)` after `close = float(event.close)`.
- Replaced the price-only accumulation check with the median-gated version:
  `recent_vol` = sum of the last 2 bars' volume; `prior_vol` = all bars before
  those; `avg_vol` = `prior_vol.median()` (empty guard -> 0.0); `vol_ok` =
  `avg_vol <= 0 or recent_vol >= self.accum_volume_mult * avg_vol`; accumulation
  requires `elapsed >= 2`, POC proximity (`abs(close - poc) <= 2*step`), AND `vol_ok`.

`tests/test_valentini_leg_anchor.py`: appended `test_accumulation_requires_recent_volume`
verbatim from the brief.

## Concerns

- None blocking. Verified median (not mean) is used, matching the design note:
  a mean baseline would have been inflated by the 1500-volume absorption bar and
  rejected the 300-volume confirmation bar, but median keeps 300 >= 1.5*100 true.
- The zero-parity path (3000+1000=4000 vs prior median ~1000) still confirms, so
  entry at `111.0` is unchanged — 3 passed confirms no behavioural drift.

## Fix subagent: review follow-ups (comment + ATR-bound test)

- `ntrade/engines/strategies.py`: profile-build comment now reads
  `>= leg_impulse_mult * self._step` (the ATR-floored step) instead of the
  stale `range_size`, keeping the `ponytail:` note intact.
- `tests/test_valentini_leg_anchor.py`: added
  `test_impulse_reanchors_leg_when_atr_floor_binds` — `range_size=1.0` with
  wide volatile bars (high-low 5.0, ATR ~5.0) proves `_step > range_size`
  (ATR floor binds) then a 14.0-span impulse re-anchors the leg to row 30.
  Note: initial span 12.0 failed (warmup bars had span 7.0 → ATR 7.0 → thr 14.0);
  switched warmup to span 5.0 (ATR ~5.0, thr ~10.0) and impulse to span 14.0.

Test results:
- `python -m pytest tests/test_valentini_leg_anchor.py -q` → `4 passed`
- `python -m pytest tests/test_valentini_strategy.py -q` → `24 passed`
- `python -m pytest tests/test_zero_parity_across_modes.py -q` → `3 passed`

Commit: `b57b41ee0c0e6533571bed18a940c59ada23ce7e`
