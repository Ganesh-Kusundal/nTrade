# Task 4 Brief: Auction-following trail (runner + divergence + structure-break + session-close-first)

## Where this fits

Project: nTrade UI. The strategy mirror `ui/src/lib/valentini.ts` is being re-synced with the Python `ValentiniScalper`. Tasks 1-3 added `swingBias`, the ATR floor + leg-anchored profile, and the direction gate + volume accumulation. This task replaces the fixed R-multiple target with the auction-following trail: runner positions (`tp: null`), swing-pivot stop ratchet, volume-divergence exit, structure-break exit, and session-close checked first.

## Requirements (verbatim from the plan)

**Files:**
- Modify: `ui/src/lib/valentini.ts` (trade-management block, exit reasons, entry sizing)
- Test: `ui/src/lib/__tests__/valentini.test.ts`

**Interfaces:**
- Consumes: `step`, `rangeBars` (Task 3), `priorPoc`, `minRr`, `tpMultiplier`, `legStartIdx`, `sessionVal/Vah`.
- Produces: runner entries (`tp: null` when no prior-POC clears minRr); `ExitReason` extended to `'divergence' | 'structure_break'`; session-close checked first.

**Step 1: Write the failing tests**

Append to `ui/src/lib/__tests__/valentini.test.ts`:

```ts
describe('auction trail', () => {
  it('enters a runner (tp null) when no prior-day POC clears minRr', () => {
    const cs = [...baseSession(), absorptionBar(20, 108), candle(21, { close: 118 }), candle(22, { close: 122 })]
    const res = runValentini(cs, { ...OPTS })
    const t = res.trades.find((t) => t.side === 'BUY')
    expect(t).toBeDefined()
    expect(t!.tp).toBeNull()
  })
  it('session close wins over structure break', () => {
    const cs = [
      ...baseSession(), absorptionBar(20, 108), candle(21, { close: 118 }), candle(22, { close: 122 }),
      // post-session candle that would also read as a structure break
      candle(23, { close: 116, open: 122, high: 123, low: 115, ts: OUTSIDE }),
    ]
    const res = runValentini(cs, { ...OPTS, sessionEnd: '15:25' })
    const t = res.trades.find((t) => t.side === 'BUY')
    expect(t?.reason).toBe('session_close')
  })
})
```

**Step 2: Run to verify it fails**

Run: `cd ui && npm test -- __tests__/valentini.test.ts 2>&1 | tail -15`
Expected: FAIL — `tp` is the R-multiple (not null); session-close is not first.

**Step 3: Extend `ExitReason` and the runner entry**

`valentini.ts:36` — change `ExitReason` to:

```ts
export type ExitReason = 'stop' | 'target' | 'divergence' | 'structure_break' | 'session_close'
```

Update the `active` type (currently `valentini.ts:204`) to include `impulseVolume: number` and `tp: number | null`:

```ts
  let active: { side: 'BUY' | 'SELL'; entry: number; sl: number; tp: number | null; rr: number; impulseVolume: number } | null = null
```

In the signal block (currently the `let tp = side === 'BUY' ? ...` at `valentini.ts:425`), replace the SL/TP computation so the entry is a runner unless prior-POC clears `minRr`:

```ts
        const entry = close(i)
        const sl = side === 'BUY'
          ? (sessionVah > sessionVal ? sessionVal - step : lastAbsorption.price - step)
          : (sessionVah > sessionVal ? sessionVah + step : lastAbsorption.price + step)
        const rrFb = side === 'BUY'
          ? (entry > sl ? ((entry + (entry - sl) * tpMultiplier) - entry) / (entry - sl) : 0)
          : (sl > entry ? (entry - ((sl - entry) * tpMultiplier)) / (sl - entry) : 0)
        let tp: number | null = null
        let rr = rrFb
        if (priorPoc !== null) {
          const rrPoc = side === 'BUY'
            ? (entry > sl && priorPoc > entry ? (priorPoc - entry) / (entry - sl) : 0)
            : (sl > entry && priorPoc < entry ? (entry - priorPoc) / (sl - entry) : 0)
          if (rrPoc >= minRr) { tp = priorPoc; rr = rrPoc }
        }
        if (rr >= minRr) {
          const impulseVolume = dayBars.slice(legStartIdx).reduce((a, b) => a + (b.volume || 0), 0)
          trades.push({ side, entryIndex: i, entry, sl, tp, rr, exitIndex: null, exit: null, reason: null })
          active = { side, entry, sl, tp, rr, impulseVolume }
        }
```

**Step 4: Rewrite the trade-management block**

Replace the `if (active) { ... }` block with the session-close-first + divergence + structure-break + trail version:

```ts
    if (active) {
      const low = c.low
      const high = c.high
      // Hard session close FIRST: the gate is absolute and wins over the
      // conditional auction exits (mirror of strategies.py _manage_exit).
      if (!inSession(i)) {
        trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: close(i), reason: 'session_close' }
        active = null
        continue
      }
      if (active.side === 'BUY') {
        if (low <= active.sl) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.sl, reason: 'stop' }
          active = null
        } else if (active.tp !== null && high >= active.tp) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.tp, reason: 'target' }
          active = null
        } else if (divergenceExit('BUY')) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: close(i), reason: 'divergence' }
          active = null
        } else if (structureBroken('BUY')) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: close(i), reason: 'structure_break' }
          active = null
        } else if (high >= active.entry + (opts.trailArmMult ?? 1.0) * Math.abs(active.entry - active.sl)) {
          const pivot = lastSwingLow()
          if (pivot !== null && pivot > active.sl) active.sl = pivot
        }
      } else {
        if (high >= active.sl) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.sl, reason: 'stop' }
          active = null
        } else if (active.tp !== null && low <= active.tp) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.tp, reason: 'target' }
          active = null
        } else if (divergenceExit('SELL')) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: close(i), reason: 'divergence' }
          active = null
        } else if (structureBroken('SELL')) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: close(i), reason: 'structure_break' }
          active = null
        } else if (low <= active.entry - (opts.trailArmMult ?? 1.0) * Math.abs(active.entry - active.sl)) {
          const pivot = lastSwingHigh()
          if (pivot !== null && pivot < active.sl) active.sl = pivot
        }
      }
      continue
    }
```

Add the helper closures after the `direction` closure (after the direction function body):

```ts
  const doneRangeBars = () => rangeBars.filter((b) => b.isComplete)
  const lastSwingLow = (): number | null => {
    const d = doneRangeBars()
    if (d.length < 2) return null
    return d[d.length - 1].low > d[d.length - 2].low ? d[d.length - 1].low : null
  }
  const lastSwingHigh = (): number | null => {
    const d = doneRangeBars()
    if (d.length < 2) return null
    return d[d.length - 1].high < d[d.length - 2].high ? d[d.length - 1].high : null
  }
  const divergenceExit = (side: 'BUY' | 'SELL'): boolean => {
    const d = doneRangeBars()
    if (d.length < 2) return false
    const p = d[d.length - 2]
    const c2 = d[d.length - 1]
    const impVol = active?.impulseVolume ?? 0
    if (impVol <= 0) return false
    if ((c2.volume || 0) >= (opts.divergenceVolumeMult ?? 0.6) * impVol) return false
    return side === 'BUY' ? c2.high > p.high : c2.low < p.low
  }
  const structureBroken = (side: 'BUY' | 'SELL'): boolean => {
    const d = doneRangeBars()
    if (d.length < 2) return false
    const p = d[d.length - 2]
    const c2 = d[d.length - 1]
    return side === 'BUY' ? c2.close < p.low : c2.close > p.high
  }
```

**Step 5: Run the full TS suite**

Run: `cd ui && npm test 2>&1 | tail -6`
Expected: all pass. If an existing test (`test_stop_wins_when_bar_hits_both_stop_and_target`, `test_hard_session_close`) fails, verify the ordering matches Python (session-close first, stop before divergence/structure) and tune only if genuinely needed.

Run: `cd ui && npm run typecheck 2>&1 | tail -4` — no type errors.

**Step 6: Commit**

```bash
git add ui/src/lib/valentini.ts ui/src/lib/__tests__/valentini.test.ts
git commit -m "feat: ts valentini auction-following trail"
```

## Global Constraints (apply to this task)

- Modify ONLY `ui/src/lib/valentini.ts` and `ui/src/lib/__tests__/valentini.test.ts`.
- Do NOT touch `TradeScreen.tsx`, `ChartPanel.tsx`, or any other file.
- Defaults: `trailArmMult=1.0`, `divergenceVolumeMult=0.6`.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-4-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the vitest output lines, and any concerns.
