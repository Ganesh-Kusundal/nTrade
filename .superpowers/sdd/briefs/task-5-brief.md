# Task 5 Brief: PnL-gated reversal to leg POC

## Where this fits

Project: nTrade UI. The strategy mirror `ui/src/lib/valentini.ts` is being re-synced with the Python `ValentiniScalper`. Tasks 1-4 added `swingBias`, ATR floor + leg-anchored profile, direction gate + volume accumulation, and the auction-following trail. This task adds the PnL-gated reversal (secondary mean-reversion setup): only armed after a profitable day, fires on overextension + absorption at the extreme + response back toward the leg POC.

## Requirements (from the plan, with a correctness fix)

**Files:**
- Modify: `ui/src/lib/valentini.ts` (reversal state + dispatch)
- Test: `ui/src/lib/__tests__/valentini.test.ts`

**Interfaces:**
- Consumes: `dayPnl` (accumulated from closed trades), `sessionPoc`, `step`, `reverseExtensionMult`, `lastAbsorption`, `active`.
- Produces: reversal trades whose `tp === sessionPoc` (the computed leg POC) and `reason` null.

## Correctness note (plan fix)

A naive PnL accumulation ("if last.exit !== null, add PnL") would RE-ADD the same PnL on every subsequent bar: the `if (active)` block `continue`s past the accumulation on the closing bar, and `last.exit` stays non-null forever. Use a settled-counter instead.

**Step 1: Write the failing tests**

Append to `ui/src/lib/__tests__/valentini.test.ts`:

```ts
describe('reversal', () => {
  // The mirror recomputes the session POC per candle; the reversal targets the
  // *computed* POC (baseSession's heavy centre keeps it ~112), so assertions
  // check that a reversal trade fired (non-null POC target), not a magic 118.
  it('does NOT arm a reversal without day profit', () => {
    const cs = [
      ...baseSession(),
      ...Array.from({ length: 35 }, (_, k) => candle(20 + k, { close: 120 - k * 0.5 })), // downtrend
      absorptionBar(55, 90),
      candle(56, { close: 92 }),
    ]
    const res = runValentini(cs, { ...OPTS })
    const reversal = res.trades.find((t) => t.tp !== null && t.reason === null && t.exitIndex === null)
    expect(reversal).toBeUndefined()
  })
  it('fires a BUY reversal to the leg POC after a profitable day', () => {
    const cs = [
      ...baseSession(),
      ...Array.from({ length: 35 }, (_, k) => candle(20 + k, { close: 120 - k * 0.5 })),
      absorptionBar(55, 90),
      candle(56, { close: 92 }),
    ]
    // White-box: seed a profitable day, mirroring how the Python strategy
    // test sets `strat._day_pnl = 5000.0`. The mirror is a pure function
    // with no broker fills, so it can't manufacture a winning round-trip
    // from the fixture — day PnL is seeded via the option.
    const res = runValentini(cs, { ...OPTS, initialDayPnl: 5000 })
    const reversal = res.trades.find((t) => t.side === 'BUY' && t.tp !== null && t.reason === null)
    expect(reversal).toBeDefined()
    // Reversal targets the computed leg POC (below entry on a BUY fade).
    expect(reversal!.tp).toBeLessThan(reversal!.entry)
  })
})
```

**Step 2: Run to verify it fails**

Run: `cd ui && npm test -- __tests__/valentini.test.ts 2>&1 | tail -15`
Expected: FAIL — no reversal trade fires in the second test.

**Step 3: Add day-PnL tracking and the reversal dispatch**

Add to `ValentiniOptions`:

```ts
  /** Starting realized day PnL (white-box; the mirror has no broker fills, so
   *  tests seed it — mirrors Python's `_day_pnl = 5000.0` white-box setup). */
  initialDayPnl?: number
```

Add machine state (with the other `let` state, near `let legStartIdx = 0`):

```ts
  let dayPnl = opts.initialDayPnl ?? 0
  let dayPnlSettled = 0
```

In the day-change block (currently `valentini.ts:306-326`, the `if (key !== dayKey)` block that sets `phase = 'waiting'` and resets `dayBars`, `legStartIdx`, `sessionVwap`), add resets:

```ts
      dayPnl = opts.initialDayPnl ?? 0
      dayPnlSettled = 0
```

Add the settlement loop AFTER the `if (active)` block (i.e. after its `continue`, before the warm-up/session gate at `valentini.ts:399`):

```ts
    // Settle any closed trades' PnL once (the reversal gate reads dayPnl).
    while (dayPnlSettled < trades.length && trades[dayPnlSettled].exit !== null) {
      const t = trades[dayPnlSettled]
      dayPnl += t.side === 'BUY' ? (t.exit! - t.entry) : (t.entry - t.exit!)
      dayPnlSettled++
    }
```

Add the reversal dispatch after the warm-up/session gate (after `if (i < warmup || !inSession(i)) continue`):

```ts
    if (dayPnl > 0 && maybeReverse(i)) continue
```

Add `maybeReverse` after the `structureBroken` closure:

```ts
  const maybeReverse = (idx: number): boolean => {
    if (dayPnl <= 0 || active !== null) return false
    if (sessionPoc <= 0 || lastAbsorption === null) return false
    const a = lastAbsorption
    const close = candles[idx].close
    const ext = (opts.reverseExtensionMult ?? 2.0) * step
    if (a.side === 'SELL' && close > sessionPoc + ext && close < a.price) {
      const sl = a.price + step
      if (sl > close) { trades.push({ side: 'SELL', entryIndex: idx, entry: close, sl, tp: sessionPoc, rr: 0, exitIndex: null, exit: null, reason: null }); active = { side: 'SELL', entry: close, sl, tp: sessionPoc, rr: 0, impulseVolume: 0 }; return true }
    } else if (a.side === 'BUY' && close < sessionPoc - ext && close > a.price) {
      const sl = a.price - step
      if (sl < close) { trades.push({ side: 'BUY', entryIndex: idx, entry: close, sl, tp: sessionPoc, rr: 0, exitIndex: null, exit: null, reason: null }); active = { side: 'BUY', entry: close, sl, tp: sessionPoc, rr: 0, impulseVolume: 0 }; return true }
    }
    return false
  }
```

Note: `maybeReverse` sets `active` directly (like Python's `_emit_reversal` sets `_pending`). The `continue` from the dispatch line prevents the continuation chain from also running on the same candle.

**Step 4: Run the full TS suite**

Run: `cd ui && npm test 2>&1 | tail -6`
Expected: all pass.

Run: `cd ui && npm run typecheck 2>&1 | tail -4` — no type errors.

**Step 5: Commit**

```bash
git add ui/src/lib/valentini.ts ui/src/lib/__tests__/valentini.test.ts
git commit -m "feat: ts valentini pnl-gated reversal to leg POC"
```

## Global Constraints (apply to this task)

- Modify ONLY `ui/src/lib/valentini.ts` and `ui/src/lib/__tests__/valentini.test.ts`.
- Do NOT touch `TradeScreen.tsx`, `ChartPanel.tsx`, or any other file.
- Default: `reverseExtensionMult=2.0`.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-5-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the vitest output lines, and any concerns.
