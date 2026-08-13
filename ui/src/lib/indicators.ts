/**
 * Client-side indicators for the chart — pure TS mirrors of the backend
 * analytics (`ntrade/domain/analytics/`), so the UI draws the same numbers
 * the Valentini (Fabio) strategy sees without a server round-trip:
 *
 *  - `vwapSeries` — per-session VWAP ± n·σ bands (session = IST trading day;
 *    a fresh accumulation each day, the standard intraday VWAP).
 *  - `buildVolumeProfile` — horizontal volume distribution (POC / 68% VA),
 *    the strategy's "location" step.
 *  - `detectAbsorptions` — "big volume, no price" bars, the strategy's
 *    phase-1 signal.
 *
 * Parameter defaults match the strategy's (abs_volume_mult 1.5,
 * range_threshold 0.5, window 20, VALUE_AREA_PCT 0.68). All pure + tested.
 */

import type { Candle } from '../types/market'
import { IST_OFFSET_S, istDateKey } from './istTime'

// ---------------------------------------------------------------------------
// VWAP + bands (per IST session day)
// ---------------------------------------------------------------------------

export interface VwapPoint {
  time: number // wire UTC epoch seconds
  value: number
}

export interface VwapSeriesResult {
  vwap: VwapPoint[]
  upper: VwapPoint[]
  lower: VwapPoint[]
}

/**
 * Per-session VWAP with n·σ volume-weighted standard-deviation bands,
 * reset at each IST trading day boundary (like the backend's `vwap_bands`,
 * but a full series instead of just the last bar). Bars with zero volume
 * contribute no point (the backend yields NaN there).
 */
export function vwapSeries(candles: Candle[], numStd = 2): VwapSeriesResult {
  const vwap: VwapPoint[] = []
  const upper: VwapPoint[] = []
  const lower: VwapPoint[] = []
  let sessionKey = ''
  let cumPV = 0
  let cumVol = 0
  let cumVar = 0
  for (const c of candles) {
    const key = istDateKey(c.time)
    if (key !== sessionKey) {
      sessionKey = key
      cumPV = 0
      cumVol = 0
      cumVar = 0
    }
    const vol = c.volume || 0
    if (vol <= 0) continue
    const typical = (c.high + c.low + c.close) / 3
    cumVol += vol
    cumPV += typical * vol
    const v = cumPV / cumVol
    cumVar += (typical - v) ** 2 * vol
    const sigma = Math.sqrt(cumVar / cumVol)
    vwap.push({ time: c.time, value: v })
    upper.push({ time: c.time, value: v + numStd * sigma })
    lower.push({ time: c.time, value: v - numStd * sigma })
  }
  return { vwap, upper, lower }
}

// ---------------------------------------------------------------------------
// Volume profile (POC / value area)
// ---------------------------------------------------------------------------

export interface VpLevel {
  price: number // bucket centre
  volume: number
}

export interface VolumeProfile {
  levels: VpLevel[]
  poc: number // Point of Control (busiest bucket centre)
  vah: number // Value Area High
  val: number // Value Area Low
  step: number // bucket width
}

const VALUE_AREA_PCT = 0.68

/**
 * Fixed-range slice for the volume profile: every bar of the CURRENT session
 * day (the IST date of the last bar) at/after the session open — ``"09:15"``
 * for NSE/NFO, ``"09:00"`` for MCX. This is the TradingView FRVP behaviour:
 * the range is FIXED, so zooming/panning does not change the profile; it only
 * grows as the live session prints new bars. Bars before the open on the
 * anchor day are excluded (they are not part of the session), as are all
 * earlier days.
 */
export function sessionProfileCandles(candles: Candle[], openHHMM: string): Candle[] {
  if (candles.length === 0) return []
  const [h, m] = openHHMM.split(':').map(Number)
  const openMin = h * 60 + m
  const anchor = istDateKey(candles[candles.length - 1].time)
  const out: Candle[] = []
  for (let i = candles.length - 1; i >= 0; i--) {
    const c = candles[i]
    if (istDateKey(c.time) !== anchor) break
    const istMin = Math.floor((c.time + IST_OFFSET_S) / 60) % (24 * 60)
    if (istMin >= openMin) out.unshift(c)
  }
  return out
}

/**
 * Build a volume profile over a candle set — each bar's volume allocated to
 * the bucket containing its midpoint (high+low)/2, buckets centred on
 * multiples of `step` (half-up rounding, matching the backend). POC = busiest
 * bucket; value area expands outward from the POC until 68% of volume.
 */
export function buildVolumeProfile(candles: Candle[], step?: number): VolumeProfile {
  if (candles.length === 0) {
    return { levels: [], poc: 0, vah: 0, val: 0, step: step ?? 0 }
  }
  const span = Math.max(...candles.map((c) => c.high)) - Math.min(...candles.map((c) => c.low))
  let width = step && step > 0 ? step : 1.0
  if (!(step && step > 0)) {
    width = span > 0 ? Math.max(Math.round((span / 50) * 100) / 100, 0.05) : 1.0
  }
  const bucketIndex = (price: number): number => Math.floor(price / width + 0.5)
  const kStart = bucketIndex(Math.min(...candles.map((c) => c.low)))
  const kEnd = Math.max(kStart, bucketIndex(Math.max(...candles.map((c) => c.high))))
  const n = kEnd - kStart + 1
  const vols = new Array<number>(n).fill(0)
  for (const c of candles) {
    const mid = (c.high + c.low) / 2
    const b = Math.max(0, Math.min(bucketIndex(mid) - kStart, n - 1))
    vols[b] += c.volume || 0
  }
  const total = vols.reduce((a, b) => a + b, 0)
  if (total <= 0) return { levels: [], poc: 0, vah: 0, val: 0, step: width }

  let pocIdx = 0
  for (let b = 1; b < n; b++) if (vols[b] > vols[pocIdx]) pocIdx = b

  // Value area: expand from the POC toward the higher-volume neighbour.
  const included = new Set([pocIdx])
  let acc = vols[pocIdx]
  let left = pocIdx - 1
  let right = pocIdx + 1
  while (acc < VALUE_AREA_PCT * total && (left >= 0 || right < n)) {
    let pick: number
    if (left < 0) pick = right
    else if (right >= n) pick = left
    else pick = vols[left] >= vols[right] ? left : right
    included.add(pick)
    acc += vols[pick]
    if (pick === left) left--
    else right++
  }

  const levels: VpLevel[] = []
  for (let b = 0; b < n; b++) {
    levels.push({ price: Math.round((kStart + b) * width * 10000) / 10000, volume: vols[b] })
  }
  return {
    levels,
    poc: Math.round((kStart + pocIdx) * width * 10000) / 10000,
    vah: Math.round((kStart + Math.max(...included)) * width * 10000) / 10000,
    val: Math.round((kStart + Math.min(...included)) * width * 10000) / 10000,
    step: width,
  }
}

// ---------------------------------------------------------------------------
// Absorption ("big volume, no price")
// ---------------------------------------------------------------------------

export interface Absorption {
  barIndex: number
  price: number
  volume: number
  side: 'BUY' | 'SELL'
  strength: number // 0..1 normalized volume excess
}

export interface AbsorptionOptions {
  avgVolumeMult?: number
  rangeThreshold?: number
  /** When given, the range limit is `rangeThreshold × rangeSize` (the
   *  strategy's fixed threshold) instead of the self-scaling rolling mean. */
  rangeSize?: number
  window?: number
}

/**
 * Flag absorption bars — volume ≥ avgVolumeMult × trailing average AND a
 * compressed range. Side from close-vs-open. Mirrors the backend
 * `detect_absorptions`: when `rangeSize` is given the range limit is the
 * fixed `rangeThreshold × rangeSize`; otherwise it is self-scaling
 * (rangeThreshold × trailing average range).
 */
export function detectAbsorptions(
  candles: Candle[],
  opts: AbsorptionOptions = {},
): Absorption[] {
  const avgVolumeMult = opts.avgVolumeMult ?? 1.5
  const rangeThreshold = opts.rangeThreshold ?? 0.5
  const window = opts.window ?? 20
  if (candles.length === 0) return []
  const vols = candles.map((c) => c.volume || 0)
  const ranges = candles.map((c) => c.high - c.low)
  const volsMean = vols.reduce((a, b) => a + b, 0) / vols.length
  const rangesMean = ranges.reduce((a, b) => a + b, 0) / ranges.length

  const out: Absorption[] = []
  for (let i = 0; i < candles.length; i++) {
    const prev = vols.slice(Math.max(0, i - window), i)
    let avg = prev.length ? prev.reduce((a, b) => a + b, 0) / prev.length : vols[0]
    if (avg <= 0) avg = volsMean
    const vol = vols[i]
    if (avg <= 0 || vol < avgVolumeMult * avg) continue

    const limit = opts.rangeSize && opts.rangeSize > 0
      ? rangeThreshold * opts.rangeSize
      : rangeThreshold * (() => {
          const prevR = ranges.slice(Math.max(0, i - window), i)
          let avgRange = prevR.length ? prevR.reduce((a, b) => a + b, 0) / prevR.length : ranges[0]
          return avgRange > 0 ? avgRange : rangesMean
        })()
    if (ranges[i] > limit) continue

    const c = candles[i]
    const side: 'BUY' | 'SELL' = c.close >= c.open ? 'BUY' : 'SELL'
    const excess = avgVolumeMult > 1 ? (vol / avg - 1) / (avgVolumeMult - 1) : 1
    out.push({
      barIndex: i,
      price: c.close,
      volume: vol,
      side,
      strength: Math.max(0, Math.min(1, excess)),
    })
  }
  return out
}
