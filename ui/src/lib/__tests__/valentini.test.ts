import { describe, expect, it } from 'vitest'
import type { Candle } from '../../types/market'
import { swingBias } from '../rangeBars'
import { runValentini } from '../valentini'

// Session base: 2026-08-03 10:00 IST (04:30 UTC) — inside 09:15–15:25.
const T0 = Date.UTC(2026, 7, 3, 4, 30) / 1000
const OUTSIDE = Date.UTC(2026, 7, 3, 10, 30) / 1000 // 16:00 IST — after the gate

function candle(i: number, opts: {
  close?: number; open?: number; high?: number; low?: number; volume?: number; ts?: number
} = {}): Candle {
  const c = opts.close ?? 100 + i * 0.5
  const o = opts.open ?? c - 0.5
  return {
    time: opts.ts ?? T0 + i * 60,
    open: o,
    high: opts.high ?? Math.max(c, o) + 0.5,
    low: opts.low ?? Math.min(c, o) - 0.5,
    close: c,
    volume: opts.volume ?? 100,
  }
}

// A bar whose midpoint sits at `m` (so the volume profile buckets it at m).
function bar(i: number, m: number, opts: { volume?: number; ts?: number } = {}): Candle {
  return candle(i, { close: m, open: m - 0.1, high: m + 0.4, low: m - 0.4, volume: opts.volume ?? 100, ts: opts.ts })
}

// Build a session whose volume profile is centred: POC=112, VAL=108, VAH=116
// (step=4). Centre bars carry heavy volume so the absorption bar (at the edge)
// cannot pull the POC away from the centre.
function baseSession(): Candle[] {
  return [
    ...Array.from({ length: 10 }, (_, k) => bar(k, 112, { volume: 500 })),
    ...Array.from({ length: 3 }, (_, k) => bar(10 + k, 108)),
    ...Array.from({ length: 3 }, (_, k) => bar(13 + k, 116)),
    ...Array.from({ length: 4 }, (_, k) => bar(16 + k, 112, { volume: 500 })), // pad past warmup
  ]
}

function absorptionBar(i: number, at: number, ts?: number): Candle {
  return candle(i, { close: at, open: at - 0.02, high: at + 0.05, low: at - 0.05, volume: 1500, ts })
}

// VAL=108, POC=112, VAH=116, step=4. Breakouts sit above VAH (outside the
// value area) so they are not blocked by the balance gate.
const OPTS = { rangeSize: 4.0, warmup: 15, tpMultiplier: 2.0, minRr: 1.5, fadeExtended: false }

function buys(result: ReturnType<typeof runValentini>): number {
  return result.trades.filter((t) => t.side === 'BUY').length
}

describe('valentini state machine', () => {
  it('waits without an absorption', () => {
    const r = runValentini(baseSession(), OPTS)
    expect(r.phase).toBe('waiting')
    expect(r.trades).toHaveLength(0)
  })

  it('reaches absorbing when the absorption sits at the value edge (VAL)', () => {
    const bars = [...baseSession(), absorptionBar(20, 108)]
    expect(runValentini(bars, OPTS).phase).toBe('absorbing')
  })

  it('does NOT arm an absorption away from the value edge', () => {
    const bars = [...baseSession(), absorptionBar(20, 124)] // far above VAH
    expect(runValentini(bars, OPTS).phase).toBe('waiting')
  })

  it('emits a BUY after absorption → accumulation at POC → aggression above VWAP', () => {
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108),     // BUY absorption at VAL
      bar(21, 112),               // consolidate at POC
      bar(22, 120),               // break above VAH + VWAP -> signal
    ]
    const r = runValentini(bars, OPTS)
    const t = r.trades[0]
    expect(t.side).toBe('BUY')
    expect(t.entryIndex).toBe(22)
    // SL = VAL - step = 108 - 4; TP = 2R from entry (no prior POC in one day).
    expect(t.sl).toBeCloseTo(108.0 - 4.0, 6)
    expect(t.tp).toBeCloseTo(t.entry + (t.entry - t.sl) * 2.0, 6)
    expect(t.rr).toBeCloseTo(2.0, 6)
  })

  it('blocks the signal when price is below VWAP (downtrend)', () => {
    const bars = [
      ...Array.from({ length: 35 }, (_, i) => candle(i, { close: 120.0 - i * 0.5 })),
      absorptionBar(35, 100.0),
      candle(36, { close: 101.8 }),
      candle(37, { close: 101.6 }),
    ]
    const r = runValentini(bars, OPTS)
    expect(buys(r)).toBe(0)
  })

  it('does not enter without an absorption', () => {
    expect(runValentini([...baseSession(), bar(20, 112)], OPTS).trades).toHaveLength(0)
  })

  it('does not double-enter while a position is open', () => {
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108),
      bar(21, 112),
      bar(22, 120),
    ]
    for (let i = 23; i < 30; i++) bars.push(bar(i, 122 + i))
    const r = runValentini(bars, OPTS)
    expect(buys(r)).toBe(1)
  })

  it('exits at the stop when price drops through it', () => {
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108),
      bar(21, 112),
      bar(22, 120),
      bar(23, 100.0, { volume: 500 }), // through SL = 104
    ]
    const r = runValentini(bars, OPTS)
    const t = r.trades[0]
    expect(t.reason).toBe('stop')
    expect(t.exitIndex).toBe(23)
    expect(t.exit).toBeCloseTo(t.sl, 6)
  })

  it('stop wins when a bar hits both stop and target', () => {
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108),
      bar(21, 112),
      bar(22, 120), // BUY entry; SL=104, TP≈136
      candle(23, { close: 110.0, open: 120.0, high: 140.0, low: 100.0 }),
    ]
    const r = runValentini(bars, OPTS)
    const t = r.trades[0]
    expect(t.reason).toBe('stop') // protective order wins
    expect(r.trades).toHaveLength(1) // no double exit
  })

  it('hard session close exits an open position', () => {
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108),
      bar(21, 112),
      bar(22, 120), // BUY entry
      bar(23, 122, { ts: OUTSIDE + 60 }),
    ]
    const r = runValentini(bars, OPTS)
    const t = r.trades[0]
    expect(t.reason).toBe('session_close')
    expect(t.exit).toBeCloseTo(122, 6)
    expect(r.phase).toBe('waiting')
  })

  it('waits for a pullback when the trigger chases beyond the ±2σ band', () => {
    const extOpts = { ...OPTS, fadeExtended: true }
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108),
      bar(21, 112),
      bar(22, 125, { volume: 1000 }), // extended beyond VWAP +2σ
      bar(23, 118),                    // pullback inside the band -> fires
    ]
    const r = runValentini(bars, extOpts)
    const entries = r.trades.filter((t) => t.side === 'BUY')
    expect(entries.map((t) => t.entryIndex)).toEqual([23])
  })

  it('expires the absorbing setup when price runs away, then re-arms', () => {
    const bars = [...baseSession(), absorptionBar(20, 108)]
    for (let i = 21; i < 37; i++) bars.push(bar(i, 120.0 + (i - 21) * 5.0))
    expect(runValentini(bars, OPTS).phase).toBe('waiting')
    bars.push(absorptionBar(37, 108))
    expect(runValentini(bars, OPTS).phase).toBe('absorbing')
  })

  it('session gate blocks setups outside trading hours', () => {
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108, OUTSIDE),
      bar(21, 112, { ts: OUTSIDE + 60 }),
      bar(22, 120, { ts: OUTSIDE + 120 }),
    ]
    const r = runValentini(bars, OPTS)
    expect(r.trades).toHaveLength(0)
    expect(r.phase).toBe('waiting')
  })

  it('explicit range size drives the SL distance', () => {
    const bars = [
      ...baseSession(),
      absorptionBar(20, 108),
      bar(21, 112),
      bar(22, 120),
    ]
    const r = runValentini(bars, { ...OPTS, rangeSize: 8.0 })
    const t = r.trades[0]
    expect(t.sl).toBeCloseTo(108.0 - 8.0, 6)
    expect(t.rr).toBeCloseTo(2.0, 6)
  })

  it('returns an empty result for an empty input', () => {
    const r = runValentini([])
    expect(r).toEqual({ phase: 'waiting', trades: [], lastAbsorption: null })
  })

  it('intraday: force-closes an open trade at the day boundary (no out-of-session bars)', () => {
    const day1 = [
      ...baseSession(),
      absorptionBar(20, 108),
      bar(21, 112),
      bar(22, 120),
    ]
    const T1 = Date.UTC(2026, 7, 4, 3, 45) / 1000 // 09:15 IST (next day)
    const day2 = Array.from({ length: 20 }, (_, i) =>
      bar(23 + i, 122 + i * 0.5, { ts: T1 + i * 60 }),
    )
    const r = runValentini([...day1, ...day2], OPTS)
    const t = r.trades[0]
    expect(t.reason).toBe('session_close')
    expect(t.exitIndex).toBe(22)
    // Yesterday's absorption is out of the recency window by day — no re-arm.
    expect(r.trades).toHaveLength(1)
    expect(r.phase).toBe('waiting')
  })

  it('intraday: never arms today from a prior day absorption in the recency window', () => {
    const day1 = [...baseSession().slice(0, 16), absorptionBar(16, 108), bar(17, 110)]
    const T1 = Date.UTC(2026, 7, 4, 3, 45) / 1000 // 09:15 IST
    const day2 = Array.from({ length: 20 }, (_, i) =>
      bar(18 + i, 112 + i * 0.5, { ts: T1 + i * 60 }),
    )
    const r = runValentini([...day1, ...day2], OPTS)
    expect(r.trades).toHaveLength(0)
    expect(r.phase).toBe('waiting')
  })

  it('intraday: a fresh absorption on the next day arms its own setup', () => {
    const day1 = [...baseSession(), absorptionBar(20, 108)]
    const T1 = Date.UTC(2026, 7, 4, 3, 45) / 1000 // 09:15 IST
    const day2Base = Array.from({ length: 20 }, (_, j) =>
      bar(21 + j, 112, { ts: T1 + j * 60 }),
    )
    const day2 = [
      ...day2Base,
      absorptionBar(41, 108, T1 + 20 * 60),
      bar(42, 112, { ts: T1 + 21 * 60 }),
      bar(43, 120, { ts: T1 + 22 * 60 }),
    ]
    const r = runValentini([...day1, ...day2], OPTS)
    const t = r.trades[0]
    expect(t.side).toBe('BUY')
    expect(t.entryIndex).toBe(43)
    expect(r.trades).toHaveLength(1)
  })

  it('uses the R-multiple fallback when the prior POC is not a valid target', () => {
    // Day 1 builds a profile with POC=112; day 2's BUY entry (~120) is above
    // the prior POC, so the prior POC cannot be the target -> R-multiple used.
    const day1 = [
      ...Array.from({ length: 10 }, (_, k) => bar(k, 112, { volume: 500, ts: T0 + k * 60 })),
      ...Array.from({ length: 3 }, (_, k) => bar(10 + k, 108, { ts: T0 + (10 + k) * 60 })),
      ...Array.from({ length: 3 }, (_, k) => bar(13 + k, 116, { ts: T0 + (13 + k) * 60 })),
      bar(16, 112, { volume: 500, ts: T0 + 16 * 60 }),
    ]
    const T1 = Date.UTC(2026, 7, 4, 3, 45) / 1000 // 09:15 IST
    const day2 = [
      ...Array.from({ length: 10 }, (_, j) => bar(17 + j, 112, { volume: 500, ts: T1 + j * 60 })),
      absorptionBar(27, 108, T1 + 10 * 60),
      bar(28, 112, { ts: T1 + 11 * 60 }),
      bar(29, 120, { ts: T1 + 12 * 60 }),
    ]
    const r = runValentini([...day1, ...day2], OPTS)
    const t = r.trades[0]
    expect(t.side).toBe('BUY')
    expect(t.tp).toBeCloseTo(t.entry + (t.entry - t.sl) * 2.0, 6)
  })
})

describe('swingBias', () => {
  const rb = (high: number, low: number, complete: boolean) => ({
    time: 0, open: 0, high, low, close: (high + low) / 2, volume: 1, isComplete: complete,
  })
  it('is BUY on higher high + higher low', () => {
    expect(swingBias([rb(100, 98, true), rb(101, 99, true)])).toBe('BUY')
  })
  it('is SELL on lower high + lower low', () => {
    expect(swingBias([rb(101, 99, true), rb(100, 98, true)])).toBe('SELL')
  })
  it('is null with fewer than two complete bars', () => {
    expect(swingBias([rb(100, 98, true)])).toBeNull()
    expect(swingBias([rb(100, 98, true), rb(101, 99, false)])).toBeNull()
  })
  it('is null on an inside bar', () => {
    expect(swingBias([rb(101, 98, true), rb(100, 99, true)])).toBeNull()
  })
})
