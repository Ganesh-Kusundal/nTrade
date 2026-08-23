import type { Candle } from '../types/market'

export interface AdxPoint {
  time: number
  adx: number | null
  plus_di: number | null
  minus_di: number | null
}

/**
 * Pure Wilder's Average Directional Index (ADX 14) with +DI and -DI.
 * Works deterministically across live streaming candles and replay slices.
 */
export function calculateAdx(candles: Candle[], period: number = 14): AdxPoint[] {
  if (!candles || candles.length === 0) return []
  const n = candles.length
  if (n < 2) {
    return candles.map((c) => ({
      time: c.time,
      adx: null,
      plus_di: null,
      minus_di: null,
    }))
  }

  const tr: number[] = new Array(n).fill(0)
  const plusDm: number[] = new Array(n).fill(0)
  const minusDm: number[] = new Array(n).fill(0)

  for (let i = 1; i < n; i++) {
    const curr = candles[i]
    const prev = candles[i - 1]

    const hDiff = curr.high - prev.high
    const lDiff = prev.low - curr.low

    plusDm[i] = hDiff > lDiff && hDiff > 0 ? hDiff : 0
    minusDm[i] = lDiff > hDiff && lDiff > 0 ? lDiff : 0

    const tr1 = curr.high - curr.low
    const tr2 = Math.abs(curr.high - prev.close)
    const tr3 = Math.abs(curr.low - prev.close)
    tr[i] = Math.max(tr1, tr2, tr3)
  }

  const alpha = 1 / period
  const smoothTr: number[] = new Array(n).fill(0)
  const smoothPlusDm: number[] = new Array(n).fill(0)
  const smoothMinusDm: number[] = new Array(n).fill(0)

  let sumTr = 0
  let sumPlus = 0
  let sumMinus = 0

  const plusDi: (number | null)[] = new Array(n).fill(null)
  const minusDi: (number | null)[] = new Array(n).fill(null)
  const dx: (number | null)[] = new Array(n).fill(null)

  for (let i = 1; i < n; i++) {
    if (i < period) {
      sumTr += tr[i]
      sumPlus += plusDm[i]
      sumMinus += minusDm[i]
    } else if (i === period) {
      sumTr += tr[i]
      sumPlus += plusDm[i]
      sumMinus += minusDm[i]
      smoothTr[i] = sumTr / period
      smoothPlusDm[i] = sumPlus / period
      smoothMinusDm[i] = sumMinus / period

      const str = Math.max(smoothTr[i], 1e-10)
      const pDi = (100 * smoothPlusDm[i]) / str
      const mDi = (100 * smoothMinusDm[i]) / str
      plusDi[i] = pDi
      minusDi[i] = mDi

      const diSum = Math.max(pDi + mDi, 1e-10)
      dx[i] = (100 * Math.abs(pDi - mDi)) / diSum
    } else {
      smoothTr[i] = smoothTr[i - 1] * (1 - alpha) + tr[i] * alpha
      smoothPlusDm[i] = smoothPlusDm[i - 1] * (1 - alpha) + plusDm[i] * alpha
      smoothMinusDm[i] = smoothMinusDm[i - 1] * (1 - alpha) + minusDm[i] * alpha

      const str = Math.max(smoothTr[i], 1e-10)
      const pDi = (100 * smoothPlusDm[i]) / str
      const mDi = (100 * smoothMinusDm[i]) / str
      plusDi[i] = pDi
      minusDi[i] = mDi

      const diSum = Math.max(pDi + mDi, 1e-10)
      dx[i] = (100 * Math.abs(pDi - mDi)) / diSum
    }
  }

  const adx: (number | null)[] = new Array(n).fill(null)
  let sumDx = 0
  let dxCount = 0

  for (let i = period; i < n; i++) {
    if (dx[i] == null) continue
    dxCount++
    if (dxCount < period) {
      sumDx += dx[i]!
    } else if (dxCount === period) {
      sumDx += dx[i]!
      adx[i] = sumDx / period
    } else {
      const prevAdx = adx[i - 1] ?? sumDx / period
      adx[i] = prevAdx * (1 - alpha) + dx[i]! * alpha
    }
  }

  return candles.map((c, i) => ({
    time: c.time,
    adx: adx[i] != null ? Math.round(adx[i]! * 100) / 100 : null,
    plus_di: plusDi[i] != null ? Math.round(plusDi[i]! * 100) / 100 : null,
    minus_di: minusDi[i] != null ? Math.round(minusDi[i]! * 100) / 100 : null,
  }))
}
