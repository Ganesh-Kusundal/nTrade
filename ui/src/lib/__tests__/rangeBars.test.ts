import { describe, expect, it } from 'vitest'
import type { Candle } from '../../types/market'
import { buildRangeBars, calcAutoRange, rangeSizeFromTicks } from '../rangeBars'

/** Synthetic 1m candles: each candle spans [close - step, close + step]. */
function frame(closes: number[], opts: { step?: number; vol?: number; start?: number } = {}): Candle[] {
  const { step = 5, vol = 1000, start = 1785000000 } = opts
  return closes.map((c, i) => ({
    time: start + i * 60,
    open: c,
    high: c + step,
    low: c - step,
    close: c,
    volume: vol,
  }))
}

describe('rangeSizeFromTicks', () => {
  it('multiplies ticks by the tick size', () => {
    expect(rangeSizeFromTicks(10, 0.05)).toBe(0.5)
    expect(rangeSizeFromTicks(5, 20)).toBe(100)
  })

  it('returns undefined for auto (null / missing inputs)', () => {
    expect(rangeSizeFromTicks(null)).toBeUndefined()
    expect(rangeSizeFromTicks(undefined, 0.05)).toBeUndefined()
    expect(rangeSizeFromTicks(10)).toBeUndefined() // no tick size
    expect(rangeSizeFromTicks(0, 0.05)).toBeUndefined()
    expect(rangeSizeFromTicks(-3, 0.05)).toBeUndefined()
  })
})

describe('calcAutoRange', () => {
  it('returns a positive range', () => {
    expect(calcAutoRange(frame(Array.from({ length: 30 }, (_, i) => 100 + i)))).toBeGreaterThan(0)
  })

  it('scales with ATR (wild frames get a bigger range)', () => {
    const calm = frame(Array.from({ length: 30 }, () => 100), { step: 1 })
    const wild = frame(Array.from({ length: 30 }, () => 100), { step: 10 })
    expect(calcAutoRange(wild)).not.toBeNull()
    expect(calcAutoRange(calm)).not.toBeNull()
    expect(calcAutoRange(wild)!).toBeGreaterThan(calcAutoRange(calm)!)
  })

  it('rounds to the tick grid when tick_size is given', () => {
    const r = calcAutoRange(frame(Array.from({ length: 30 }, (_, i) => 100 + i)), 14, 1, 0.05)
    expect(r).not.toBeNull()
    expect(Math.abs(r! / 0.05 - Math.round(r! / 0.05))).toBeLessThan(1e-9)
  })

  it('falls back to null (insufficient data) on empty input', () => {
    expect(calcAutoRange([])).toBeNull()
  })

  it('returns null when ATR(14) has not converged (too few bars)', () => {
    expect(calcAutoRange(frame([100, 101]))).toBeNull()
  })

  it('buildRangeBars returns [] instead of fabricating bars when ATR is unavailable', () => {
    expect(buildRangeBars(frame([100, 101]))).toEqual([])
  })
})

describe('buildRangeBars', () => {
  it('returns [] for empty input', () => {
    expect(buildRangeBars([])).toEqual([])
  })

  it('every complete bar spans at least the range size', () => {
    const closes = Array.from({ length: 40 }, (_, i) => 100 + i * 2.5)
    const bars = buildRangeBars(frame(closes, { step: 1 }), 10)
    const complete = bars.filter((b) => b.isComplete)
    expect(complete.length).toBeGreaterThanOrEqual(3)
    for (const b of complete) expect(b.high - b.low).toBeGreaterThanOrEqual(10)
  })

  it('marks the trailing partial bar incomplete (43 candles → 10 complete + 1 partial)', () => {
    const closes = Array.from({ length: 43 }, (_, i) => 100 + i * 2.0)
    const bars = buildRangeBars(frame(closes, { step: 1 }), 10)
    expect(bars.filter((b) => b.isComplete)).toHaveLength(10)
    expect(bars[bars.length - 1].isComplete).toBe(false)
  })

  it('matches the canonical path bar count (30 candles → 7 complete)', () => {
    const closes = Array.from({ length: 30 }, (_, i) => 100 + i * 2.0)
    const bars = buildRangeBars(frame(closes, { step: 1 }), 10)
    expect(bars.filter((b) => b.isComplete)).toHaveLength(7)
  })

  it('conserves volume within 1%', () => {
    const closes = Array.from({ length: 50 }, (_, i) => 100 + i * 1.5)
    const candles = frame(closes, { step: 1, vol: 1000 })
    const bars = buildRangeBars(candles, 8)
    const totalBarVol = bars.reduce((a, b) => a + b.volume, 0)
    const totalCandleVol = candles.reduce((a, b) => a + b.volume, 0)
    expect(Math.abs(totalBarVol - totalCandleVol)).toBeLessThan(totalCandleVol * 0.01)
  })

  it('an explicit range size beats the auto ATR size', () => {
    const candles = frame(Array.from({ length: 20 }, () => 100), { step: 2 })
    const auto = buildRangeBars(candles)
    const fixed = buildRangeBars(candles, 0.5)
    expect(fixed.length).toBeGreaterThan(auto.length)
  })

  it('a candle whose first segment spans exactly the range closes a complete bar', () => {
    const candles: Candle[] = [{ time: 100, open: 100, high: 110, low: 90, close: 105, volume: 1000 }]
    const bars = buildRangeBars(candles, 10)
    expect(bars.length).toBeGreaterThan(0)
    for (const b of bars) expect(b.isComplete).toBe(true)
  })

  it('times are strictly increasing even when multiple bars close in one 1m candle', () => {
    // A tiny range (0.5) closes several bars per candle — all sharing the
    // candle's timestamp unless nudged. lightweight-charts rejects duplicates.
    const candles = frame(Array.from({ length: 10 }, () => 100), { step: 2 })
    const bars = buildRangeBars(candles, 0.5)
    expect(bars.length).toBeGreaterThan(10)
    for (let i = 1; i < bars.length; i++) {
      expect(bars[i].time).toBeGreaterThan(bars[i - 1].time)
    }
  })

  it('range bars carry the candle shape (time/open/high/low/close/volume)', () => {
    const bars = buildRangeBars(frame(Array.from({ length: 20 }, (_, i) => 100 + i), { step: 1 }), 10)
    expect(bars.length).toBeGreaterThan(0)
    const b = bars[0]
    for (const k of ['time', 'open', 'high', 'low', 'close', 'volume'] as const) {
      expect(typeof b[k]).toBe('number')
    }
  })
})
