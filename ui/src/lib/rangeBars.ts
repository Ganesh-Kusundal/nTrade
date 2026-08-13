/**
 * Range bars — price-based bars from 1m OHLCV, mirroring
 * `ntrade/domain/analytics/range_bars.py` (the primitive the Valentini
 * scalper actually trades on). A range bar closes when price travels a fixed
 * `rangeSize` (high - low >= rangeSize) regardless of how many 1m candles
 * that took; each candle is traversed along a canonical path
 * (O→H→L→C for bullish, O→L→H→C for bearish) with volume distributed
 * proportionally to the path segments each bar consumed.
 */

import type { Candle } from '../types/market'

/** A range bar — same shape as a candle, plus the completeness flag. */
export interface RangeBar extends Candle {
  isComplete: boolean
}

/**
 * ATR(14) series (Wilder-style ewm, alpha = 1/period). NaN until enough bars
 * exist (min_periods = period), matching the backend's pandas ewm.
 */
export function atrSeries(candles: Candle[], period = 14): number[] {
  // pandas ewm(alpha=1/period, min_periods=period, adjust=True).mean() over
  // the true-range series — the adjust=True (default) form is a normalized
  // weighted mean y_t = Σ_{j≤t} (1-α)^(t-j) x_j / Σ_{j≤t} (1-α)^j, NOT the
  // Wilder recursion (adjust=False). The two diverge ~25% for the first
  // 30-50 bars; matching pandas keeps the ATR floor (and thus SL distance /
  // value-edge / reversal thresholds) in lockstep with the backend.
  const out: number[] = []
  const alpha = 1 / period
  let prevClose = Number.NaN
  let x = 0 // Σ (1-α)^k over all x_j (renormalized on the fly, see below)
  let w = 0 // Σ (1-α)^k weights
  const trs: number[] = []
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    const tr = Number.isNaN(prevClose)
      ? c.high - c.low
      : Math.max(c.high - c.low, Math.abs(c.high - prevClose), Math.abs(c.low - prevClose))
    prevClose = c.close
    trs.push(tr)
  }
  // Adjusted EWMA (pandas default): y_t = (x_t + (1-a)x_{t-1} + ... + (1-a)^t x_0) / (1 + (1-a) + ... + (1-a)^t).
  for (let t = 0; t < trs.length; t++) {
    const a = trs[t]
    w = 1 + (1 - alpha) * w
    x = x * (1 - alpha) + a
    out.push(t >= period - 1 ? x / w : Number.NaN)
  }
  return out
}

/**
 * Convert a user-entered range size (in ticks) to a price range. `null` /
 * missing / non-positive inputs return undefined → the caller falls back to
 * the auto ATR size. The price range is exact (ticks × tick_size), already
 * on the contract's tick grid.
 */
export function rangeSizeFromTicks(ticks: number | null | undefined, tickSize?: number): number | undefined {
  if (ticks && ticks > 0 && tickSize && tickSize > 0) return ticks * tickSize
  return undefined
}

/**
 * ATR-derived range size, optionally rounded to a tick grid. Returns `null`
 * when ATR cannot be computed (too few bars) — the caller must render an
 * "insufficient data" state instead of silently building bars with a magic
 * default size.
 */
export function calcAutoRange(
  candles: Candle[],
  atrPeriod = 14,
  multiplier = 1,
  tickSize?: number,
): number | null {
  if (candles.length === 0) return null
  const a = atrSeries(candles, atrPeriod)
  const last = a[a.length - 1]
  if (!Number.isFinite(last) || !(last > 0)) return null
  let raw = last * multiplier
  if (tickSize && tickSize > 0) raw = Math.max(tickSize, Math.round(raw / tickSize) * tickSize)
  return Math.max(raw, 0.5)
}

/**
 * Convert a 1m OHLCV frame into range bars. Each bar is labelled with the
 * timestamp of the last source candle it consumed (right edge); the trailing
 * partial bar is marked `isComplete: false`. An explicit `rangeSize` beats
 * the auto ATR size (mirrors the backend's `build_range_bars`).
 */
export function buildRangeBars(
  candles: Candle[],
  rangeSize?: number,
  opts: { atrPeriod?: number; tickSize?: number } = {},
): RangeBar[] {
  if (candles.length === 0) return []
  const size = rangeSize && rangeSize > 0
    ? rangeSize
    : calcAutoRange(candles, opts.atrPeriod ?? 14, 1, opts.tickSize)
  // Auto ATR(14) needs enough bars — without a computable size there is no
  // honest range to build; the caller shows an "insufficient data" state.
  if (size == null) return []

  const bars: RangeBar[] = []
  let cur: RangeBar | null = null

  for (const c of candles) {
    const o = c.open
    const h = c.high
    const lo = c.low
    const cl = c.close
    const vol = c.volume || 0
    const path = cl >= o ? [o, h, lo, cl] : [o, lo, h, cl]
    const segments = path.length - 1
    const volPerSeg = segments ? vol / segments : 0
    for (let i = 1; i < path.length; i++) {
      const px = path[i]
      if (cur === null) {
        cur = {
          time: c.time,
          open: path[i - 1],
          high: Math.max(path[i - 1], px),
          low: Math.min(path[i - 1], px),
          close: px,
          volume: volPerSeg,
          isComplete: false,
        }
      } else {
        cur.high = Math.max(cur.high, px)
        cur.low = Math.min(cur.low, px)
        cur.close = px
        cur.volume += volPerSeg
        cur.time = c.time
      }
      if (cur.high - cur.low >= size) {
        cur.isComplete = true // closed bars are complete; only the trailing
        bars.push(cur)        // partial (created with isComplete: false) is not
        cur = null
      }
    }
  }

  if (cur !== null) bars.push(cur) // trailing partial bar (isComplete: false)

  // lightweight-charts requires strictly increasing times, but two range bars
  // can close within the SAME 1m candle and would share its timestamp (and a
  // duplicate-time candlestick series corrupts the chart painter). Nudge a
  // duplicate forward by 1s — invisible at minute granularity, and the next
  // source candle is ≥60s away, so the series stays strictly increasing.
  for (let i = 1; i < bars.length; i++) {
    if (bars[i].time <= bars[i - 1].time) {
      bars[i] = { ...bars[i], time: bars[i - 1].time + 1 }
    }
  }
  return bars
}

/**
 * Directional vote from the last two COMPLETE range bars (mirror of the
 * backend `swing_bias`): higher high + higher low => BUY, lower high +
 * lower low => SELL, else null (fewer than two complete bars, or an
 * inside/outside bar — no vote).
 */
export function swingBias(bars: RangeBar[]): 'BUY' | 'SELL' | null {
  const done = bars.filter((b) => b.isComplete)
  if (done.length < 2) return null
  const p = done[done.length - 2]
  const c = done[done.length - 1]
  if (c.high > p.high && c.low > p.low) return 'BUY'
  if (c.high < p.high && c.low < p.low) return 'SELL'
  return null
}
