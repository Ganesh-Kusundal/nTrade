# Valentini TS Mirror Re-sync — Design

## Problem

The UI plots the strategy's entries/exits from `ui/src/lib/valentini.ts`
(`runValentini`, called at `TradeScreen.tsx:237`), a hand-maintained TS
re-implementation of `ValentiniScalper` (`ntrade/engines/strategies.py`). The
TS mirror has drifted behind the Python strategy by six features added across
the "AMT corrections" and "continuation + reversal" batches:

| Python strategy (source of truth) | TS mirror (plot) |
|---|---|
| Direction gate (`_direction`: structure+volume+VWAP) | missing — only absorption side + VWAP |
| Leg-anchored profile (impulse `leg_impulse_mult`) | session-keyed profile only |
| ATR floor (`step = max(range_size, atr)`) | `step = rangeSize` only |
| Volume-confirmed accumulation (prior **median**) | price-only |
| Auction-following trail (runner tp=None, divergence, structure-break, session-close-first) | fixed R-multiple TP + 0.5R breakeven |
| PnL-gated reversal to leg POC | missing |

Net effect: the chart draws a different strategy than backtest/live execute —
entries/SL/TP on the plot do not match the engine's fills.

## Approach

Re-sync the TS mirror to match the Python strategy exactly. The Python code is
the spec; every rule below is the port of the current
`ntrade/engines/strategies.py`.

## Changes

### `ui/src/lib/valentini.ts` — extend `ValentiniOptions` + machine

New options (defaults mirror Python):
- `directionVolumeMult: number = 1.0`
- `legImpulseMult: number = 2.0`
- `accumVolumeMult: number = 1.5`
- `trailArmMult: number = 1.0`
- `divergenceVolumeMult: number = 0.6`
- `reverseExtensionMult: number = 2.0`

New exports/helpers (new file `ui/src/lib/swing.ts` or in `rangeBars.ts`):
- `swingBias(bars: RangeBar[]): 'BUY' | 'SELL' | null` — port of Python
  `swing_bias`: latest two **complete** range bars, higher-high+higher-low →
  BUY, lower-high+lower-low → SELL, else null.

Ports inside `runValentini`:
1. **ATR floor**: `step = max(rangeSize, atrSeries(candles, atrPeriod).last)`.
2. **Leg anchor**: the last 1m candle with `high - low >= legImpulseMult * step`
   starts a new leg; the profile is built over the leg slice, not the whole day.
3. **Direction gate**: `direction(close)` = volume vote (`leg volume >=
   directionVolumeMult * prior median`) AND structure (`swingBias`) AND VWAP
   agreement, with structure-null falling back to VWAP side. Trigger requires
   `side === direction(close)`.
4. **Volume accumulation**: `absorbing → accumulating` also requires
   `recent 2-bar volume >= accumVolumeMult * prior median volume`.
5. **Auction-following trail**: entries become runners (`tp = null`) unless
   prior-day POC clears `minRr`. `_manage_exit` ordering:
   session-close first → stop → target (only if tp set) → divergence (new
   swing extreme on volume < `divergenceVolumeMult * impulseVolume`) →
   structure-break (latest complete range bar closes through prior bar's
   extreme) → swing-pivot ratchet (≥ `trailArmMult` R profit).
6. **PnL-gated reversal**: track day PnL from closed trades; `maybeReverse`
   fires only when day PnL > 0 and price is overextended (beyond
   `reverseExtensionMult * step` from leg POC) with an absorption at the
   extreme and a response back → counter-entry, SL at extreme, TP = POC.

### `ui/src/pages/TradeScreen.tsx`

No change required — `runValentini` is called with the same args; new options
use defaults. (Knobs stay internal to the mirror for now; exposing them in the
UI is out of scope.)

### Tests

`ui/src/lib/__tests__/valentini.test.ts`:
- Keep all 17 existing tests green (they exercise the pre-change paths that
  still hold — they use data where the direction gate agrees).
- Add ~10 new tests: direction-gate gating, leg-anchor re-profile,
  volume-accumulation block, runner (tp null), divergence exit,
  structure-break exit, session-close-first, reversal PnL-gated (not armed
  flat), reversal fires to POC.
- `swingBias` unit tests (BUY / SELL / null / incomplete filtered).

## Out of scope

- Exposing the new strategy knobs in the UI (settings panel) — the mirror
  uses Python-matching defaults; wiring UI controls is a separate task.
- Serving signals from the Python API instead of the mirror (option 2 from
  the brainstorm — rejected in favor of re-sync).

## Risk

- The mirror must stay behavior-identical to Python. The 17 existing TS tests
  + 1050 Python tests are the guard: after the port, both must stay green.
- Reversal and runner paths in TS are new state that the existing test
  fixtures never exercise — new tests cover them.