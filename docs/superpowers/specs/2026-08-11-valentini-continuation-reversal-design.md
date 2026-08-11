# Valentini Continuation + Reversal Corrections — Design

## Problem

The `ValentiniScalper` (ntrade/engines/strategies.py) implements the Triple-A
chain but deviates from Fabio Valentini's actual AMT model (docs/amt-model-spec.md)
in two implementable ways:

1. **No up-front Direction gate.** Direction is inferred only at the trigger
   (absorption side + close vs VWAP). Fabio establishes *who controls the
   auction* from structure + volume + auction bias FIRST, as an independent
   precondition — direction alone is insufficient, location alone is
   insufficient, both are required.
2. **Fixed R-multiple target instead of auction-following trail.** `_emit_entry`
   computes a hard `tp` (prior-POC or R-multiple) and `_manage_exit` exits at
   it. Fabio follows the auction: hold while volume+price confirm, exit on a
   structure break or volume-price divergence.

3. **No reversal model.** Fabio's secondary mean-reversion behavior (overextension
   → POC equilibrium → target POC, profit-gated) is absent.

## Changes

All in `ntrade/engines/strategies.py` (ValentiniScalper) plus one new analytics
helper. No event/kernel/execution changes. Zero-parity holds: fills remain
MARKET at `reference_price`; the strategy is the only touched code.

### Gate 0 — Direction (`_direction()`)

Computed per candle, before Location/Aggression. Returns `"BUY" | "SELL" | None`:

- **Structure** (range bars): the current leg has HH/HL (bullish) or LH/LL
  (bearish) over the last two swing pivots. A swing pivot is a completed range
  bar whose extreme (high for highs, low for lows) exceeds the preceding
  completed range bar's corresponding extreme — i.e. consecutive range bars
  making higher highs AND higher lows = bullish, lower highs AND lower lows =
  bearish. With fewer than two completed range bars, structure is indeterminate
  → direction falls back to the VWAP leg (no structure vote).
- **Volume**: the impulse leg's volume > `direction_volume_mult` × the prior
  median volume.
- **VWAP**: close above VWAP (bullish) / below VWAP (bearish).
- All three must agree → direction; else `None` (no trade).

At trigger, the continuation chain additionally requires
`side == self._direction()` (not merely absorption side + VWAP). Existing
`_extended`, `_cvd_agrees`, `_depth_blocked` remain layered on top.

### Trail — swing-pivot + volume-divergence (replaces fixed TP)

- `_emit_entry`: SL unchanged (VAL−step long / VAH+step short). Initial TP =
  prior-POC only if it clears `min_rr` (existing preference); otherwise **no
  hard TP** — the position is a runner.
- `_manage_exit`: once the position is ≥ `trail_arm_mult` × initial risk in
  profit, trail the SL to just under the last higher low (long) / above the
  last lower high (short), using range-bar swing pivots.
- **Volume-divergence exit**: price makes a new swing high (long) but the
  breakout bar volume < `divergence_volume_mult` × the impulse leg volume →
  exit.
- **Structure-break exit**: close beyond the trailed pivot → exit.
- The old `tp_multiplier` R-target becomes fallback-only (no prior-POC case);
  the divergence/structure exits are the primary path.

### Reversal — PnL-gated fade to POC

- `self._day_pnl`: realized PnL of this strategy's closed trades today
  (tracked in `_exit`; reset on session change).
- Armed only when `_day_pnl > 0`. Condition: price overextended (beyond VWAP
  band, or `reverse_extension_mult` × `_step` beyond the leg POC), a
  small-range high-volume absorption appears at the extreme
  (`detect_absorptions`), then price responds back toward the leg POC →
  counter-entry with SL at the extension extreme, TP = leg POC.
- Reversal uses a separate phase slot so it cannot fire on the same candle as a
  continuation setup.

### New knobs (all default-on to match the method)

- `direction_volume_mult: float = 1.0`
- `trail_arm_mult: float = 1.0` (R to start trailing)
- `divergence_volume_mult: float = 0.6` (breakout volume fraction of impulse)
- `reverse_extension_mult: float = 2.0`

## Data requirements

Pure 1m OHLCV + volume (already available). True aggression and live L2 depth
remain documented limitations (`docs/amt-model-spec.md` §7-8); `_depth_blocked`
stays live-only and opt-in.

## Test impact

- Existing suites must stay green: `test_valentini_strategy.py` (24),
  `test_valentini_leg_anchor.py` (4), `test_zero_parity_across_modes.py` (3).
- The Direction gate (HH/HL on range bars) **changes which setups fire** — the
  synthetic fixtures in `test_valentini_strategy.py` (already committed as WIP
  base) need tuning to produce a clear HH/HL + VWAP-bullish structure, exactly
  as prior fixture tuning was done. This is expected and sanctioned.
- New tests: `_direction` bullish/bearish/None; swing trail structure-break
  exit; volume-divergence exit; reversal armed only when day-PnL > 0; reversal
  triggers to POC; zero-parity rerun.

## Risks

- Direction gate may reduce signal count (stricter precondition) — that is the
  correct behavior per the model.
- Trailing on range-bar pivots introduces a new swing-detection dependency;
  must not fire phantom pivots on flat tape.
- Reversal must be provably profit-gated (a losing day never arms it).
