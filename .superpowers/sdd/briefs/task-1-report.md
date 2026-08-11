# Task 1 Report: Constructor params + state for the three phases

- **Status:** DONE
- **Commit:** `dfe82958c94e30b3b27ba7612205ef46bcba8b82`
- **Test result:** `24 passed in 11.58s`

## What was done

Modified `ntrade/engines/strategies.py` only:

1. Added `leg_impulse_mult: float = 2.0` and `accum_volume_mult: float = 1.5`
   params to `ValentiniScalper.__init__` after `cvd_confirm_bars: int = 3`.
2. Assigned them after `self.cvd_confirm_bars`:
   `self.leg_impulse_mult = max(1.0, float(leg_impulse_mult))` and
   `self.accum_volume_mult = max(0.0, float(accum_volume_mult))`.
3. Added state attrs `self._atr: float = 0.0`, `self._step: float = 1.0`,
   `self._leg_start_idx: int = 0` after `self._pending_age`.

Verified commit contains exactly `ntrade/engines/strategies.py`
(1 file changed, 8 insertions(+), 1 deletion(-)); the unrelated uncommitted
working-tree changes were not staged or touched.

## Concerns

None. All new attrs are inert until Tasks 2-4 consume them.
