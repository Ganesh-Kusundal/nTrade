import { describe, expect, it } from 'vitest'
import { istInputToEpoch } from '../istTime'
import type { Candle } from '../../types/market'
import { buildVolumeProfile, detectAbsorptions, sessionProfileCandles, vwapSeries } from '../indicators'

function bar(time: number, open: number, high: number, low: number, close: number, volume: number): Candle {
  return { time, open, high, low, close, volume }
}

/** Single-price bars (midpoint == price) — the backend test fixture shape. */
function flat(prices: number[], volumes: number[], t0 = 1000): Candle[] {
  return prices.map((p, i) => bar(t0 + i * 60, p, p, p, p, volumes[i]))
}

const round4 = (x: number): number => Math.round(x * 10000) / 10000

describe('buildVolumeProfile', () => {
  it('POC is the max-volume level', () => {
    const vp = buildVolumeProfile(flat([100, 101, 102, 103, 104], [100, 400, 250, 150, 50]), 1)
    expect(vp.poc).toBe(101)
  })

  it('value area captures ~68% of volume around the POC', () => {
    const prices = [100, 101, 102, 103, 104]
    const vols = [100, 400, 250, 150, 50]
    const vp = buildVolumeProfile(flat(prices, vols), 1)
    const total = vols.reduce((a, b) => a + b, 0)
    const vaVolume = vp.levels
      .filter((l) => vp.val <= l.price && l.price <= vp.vah)
      .reduce((a, l) => a + l.volume, 0)
    expect(vaVolume).toBeGreaterThanOrEqual(0.6 * total)
    expect(vaVolume).toBeLessThanOrEqual(0.75 * total)
    expect(vp.val).toBeLessThanOrEqual(vp.poc)
    expect(vp.poc).toBeLessThanOrEqual(vp.vah)
    expect(vp.val).toBeLessThan(vp.vah)
  })

  it('respects a custom step (buckets centred on multiples of step)', () => {
    const vp = buildVolumeProfile(flat([100, 102, 104, 106], [100, 100, 500, 100]), 2)
    expect(vp.step).toBe(2)
    expect(vp.poc).toBe(104)
  })

  it('levels are reported as bucket centres', () => {
    const vp = buildVolumeProfile(flat([100, 101], [100, 100]), 1)
    const centres = new Set(vp.levels.map((l) => l.price))
    expect(centres.has(100)).toBe(true)
    expect(centres.has(101)).toBe(true)
  })

  it('flat profile has a single POC level', () => {
    const vp = buildVolumeProfile(flat([100, 100, 100, 100, 100], [100, 100, 100, 100, 100]), 1)
    expect(vp.poc).toBe(100)
  })

  it('empty input yields an empty profile', () => {
    const vp = buildVolumeProfile([], 1)
    expect(vp.levels).toEqual([])
    expect(vp.poc).toBe(0)
    expect(vp.vah).toBe(0)
    expect(vp.val).toBe(0)
  })
})

describe('sessionProfileCandles', () => {
  const NSE = '09:15'
  const MCX = '09:00'

  it('returns only the last session day, from the session open onward (NSE 09:15)', () => {
    const d1 = istInputToEpoch('2026-08-05T09:00')!
    const d2 = istInputToEpoch('2026-08-06T09:00')!
    const candles = [
      bar(d1, 100, 100, 100, 100, 1),         // day1 09:00 — pre-open, earlier day
      bar(d1 + 3600, 100, 100, 100, 100, 2),  // day1 10:00 — earlier day
      bar(d2, 200, 200, 200, 200, 3),         // day2 09:00 — before the 09:15 open
      bar(d2 + 900, 200, 200, 200, 200, 4),   // day2 09:15 — first session bar
      bar(d2 + 1800, 200, 200, 200, 200, 5),  // day2 09:30
    ]
    const out = sessionProfileCandles(candles, NSE)
    expect(out.map((c) => c.time)).toEqual([d2 + 900, d2 + 1800])
  })

  it('includes the 09:00 bar for the MCX session', () => {
    const d = istInputToEpoch('2026-08-06T09:00')!
    const candles = [
      bar(d, 200, 200, 200, 200, 3),          // 09:00 — MCX session open
      bar(d + 900, 200, 200, 200, 200, 4),    // 09:15
      bar(d + 1800, 200, 200, 200, 200, 5),   // 09:30
    ]
    const out = sessionProfileCandles(candles, MCX)
    expect(out).toHaveLength(3)
  })

  it('is stable when the data ends mid-session (developing profile)', () => {
    const d = istInputToEpoch('2026-08-06T09:15')!
    const part = [
      bar(d, 100, 100, 100, 100, 1),
      bar(d + 60, 101, 101, 101, 101, 2),
    ]
    // appending a bar keeps the SAME range (same day) but grows the slice
    const full = [...part, bar(d + 120, 102, 102, 102, 102, 3)]
    expect(sessionProfileCandles(part, NSE)).toHaveLength(2)
    expect(sessionProfileCandles(full, NSE)).toHaveLength(3)
    expect(sessionProfileCandles(full, NSE).map((c) => c.time)).toEqual(
      full.map((c) => c.time))
  })

  it('empty input yields an empty slice', () => {
    expect(sessionProfileCandles([], NSE)).toEqual([])
  })
})

describe('vwapSeries', () => {
  it('accumulates VWAP within a session and resets per IST day', () => {
    const d1 = istInputToEpoch('2026-08-05T09:15')!
    const d1b = istInputToEpoch('2026-08-05T09:16')!
    const d2 = istInputToEpoch('2026-08-06T09:15')!
    const candles = [
      bar(d1, 100, 100, 100, 100, 10),
      bar(d1b, 110, 110, 110, 110, 10),
      bar(d2, 200, 200, 200, 200, 10),
    ]
    const { vwap } = vwapSeries(candles)
    expect(vwap.map((p) => p.value)).toEqual([100, 105, 200]) // session 2 starts fresh
    expect(vwap.map((p) => p.time)).toEqual([d1, d1b, d2])
  })

  it('upper band is above VWAP and lower below, widening with dispersion', () => {
    const d1 = istInputToEpoch('2026-08-05T09:15')!
    const candles = [
      bar(d1, 100, 100, 100, 100, 10),
      bar(d1 + 60, 110, 110, 110, 110, 10),
      bar(d1 + 120, 90, 90, 90, 90, 10),
    ]
    const { vwap, upper, lower } = vwapSeries(candles)
    for (let i = 0; i < vwap.length; i++) {
      expect(upper[i].value).toBeGreaterThanOrEqual(vwap[i].value - 1e-9)
      expect(lower[i].value).toBeLessThanOrEqual(vwap[i].value + 1e-9)
    }
    // First bar: zero dispersion → bands collapse onto VWAP.
    expect(round4(upper[0].value)).toBe(100)
    expect(round4(lower[0].value)).toBe(100)
    // Later bars: bands are strictly outside the VWAP.
    expect(upper[2].value).toBeGreaterThan(vwap[2].value)
    expect(lower[2].value).toBeLessThan(vwap[2].value)
  })

  it('skips zero-volume bars entirely', () => {
    const d1 = istInputToEpoch('2026-08-05T09:15')!
    const { vwap } = vwapSeries([bar(d1, 100, 100, 100, 100, 0), bar(d1 + 60, 110, 110, 110, 110, 10)])
    expect(vwap).toHaveLength(1)
    expect(vwap[0].value).toBe(110)
  })
})

describe('detectAbsorptions', () => {
  it('flags a big-volume compressed bar and skips quiet ones', () => {
    const quiet: Candle[] = Array.from({ length: 20 }, (_, i) =>
      bar(1000 + i * 60, 100, 100.5, 99.5, 100, 100))
    const spike = bar(1000 + 20 * 60, 100, 101, 100.5, 101, 400)
    const abs = detectAbsorptions([...quiet, spike])
    expect(abs).toHaveLength(1)
    expect(abs[0].barIndex).toBe(20)
    expect(abs[0].side).toBe('BUY')
    expect(abs[0].price).toBe(101)
    expect(abs[0].strength).toBe(1) // excess (3/0.5) clipped to 1
  })

  it('flags a SELL absorption when the bar closes below its open', () => {
    const quiet: Candle[] = Array.from({ length: 20 }, (_, i) =>
      bar(2000 + i * 60, 100, 100.5, 99.5, 100, 100))
    const spike = bar(2000 + 20 * 60, 101, 101, 100.5, 100.5, 400)
    const abs = detectAbsorptions([...quiet, spike])
    expect(abs).toHaveLength(1)
    expect(abs[0].side).toBe('SELL')
    expect(abs[0].price).toBe(100.5)
  })

  it('produces no absorptions on a calm series', () => {
    const calm: Candle[] = Array.from({ length: 40 }, (_, i) =>
      bar(3000 + i * 60, 100, 100.5, 99.5, 100, 100))
    expect(detectAbsorptions(calm)).toEqual([])
  })
})
