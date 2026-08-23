import { describe, it, expect } from 'vitest'
import { calculateAdx } from '../adx'
import type { Candle } from '../../types/market'

describe('calculateAdx', () => {
  it('handles empty candles safely', () => {
    expect(calculateAdx([])).toEqual([])
  })

  it('computes ADX, +DI, -DI across synthetic candles', () => {
    const candles: Candle[] = []
    let price = 24000
    for (let i = 0; i < 60; i++) {
      const open = price
      const close = price + (i % 2 === 0 ? 15 : -5)
      const high = Math.max(open, close) + 5
      const low = Math.min(open, close) - 5
      candles.push({
        time: 1700000000 + i * 60,
        open,
        high,
        low,
        close,
        volume: 1000,
      })
      price = close
    }

    const res = calculateAdx(candles, 14)
    expect(res.length).toBe(60)

    const validAdx = res.filter((p) => p.adx != null)
    expect(validAdx.length).toBeGreaterThan(20)
    for (const p of validAdx) {
      expect(p.adx).toBeGreaterThanOrEqual(0)
      expect(p.adx).toBeLessThanOrEqual(100)
      expect(p.plus_di).toBeGreaterThanOrEqual(0)
      expect(p.minus_di).toBeGreaterThanOrEqual(0)
    }
  })
})
