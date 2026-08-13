import type { Candle, TickBar } from '../types/market'
import { istDateKey } from '../lib/istTime'

/**
 * Compact per-bar ticks, keyed by bar start time. The server already groups
 * ticks per bar (`/api/market/ticks` returns `bars: TickBar[]`), so no
 * re-grouping is needed — this is just the response as a lookup map.
 */
export type TickBarMap = Map<number, TickBar>

/** Merge a live WS candle into the running set. Last-bar revise is the hot path. */
export function mergeLive(prev: Candle[], incoming: Candle): Candle[] {
  const last = prev[prev.length - 1]
  if (last && last.time === incoming.time) {
    const next = prev.slice()
    next[next.length - 1] = incoming
    return next
  }
  if (!last || incoming.time > last.time) return [...prev, incoming]
  const i = prev.findIndex((c) => c.time === incoming.time)
  if (i >= 0) {
    const next = prev.slice()
    next[i] = incoming
    return next
  }
  return [...prev, incoming].sort((a, b) => a.time - b.time)
}

/** Combine historical candles with any live WS candles (live wins by time). */
export function mergeByTime(history: Candle[], live: Candle[]): Candle[] {
  if (live.length === 0) return history
  const byTime = new Map<number, Candle>()
  for (const c of history) byTime.set(c.time, c)
  for (const c of live) byTime.set(c.time, c)
  return [...byTime.values()].sort((a, b) => a.time - b.time)
}

/** Drop the in-progress last bar — the strategy reacts to closes. */
export function closedBars(candles: Candle[]): Candle[] {
  return candles.length <= 1 ? [] : candles.slice(0, -1)
}

/**
 * Keep only the latest value until the next animation frame (setTimeout(0)
 * when rAF is missing — vitest/node).
 */
export function coalesceLatest<T>(apply: (v: T) => void): (v: T) => void {
  let pending: T | undefined
  let has = false
  let scheduled = false
  const kick = typeof requestAnimationFrame === 'function'
    ? (cb: () => void) => { requestAnimationFrame(cb) }
    : (cb: () => void) => { setTimeout(cb, 0) }
  return (v: T) => {
    pending = v
    has = true
    if (scheduled) return
    scheduled = true
    kick(() => {
      scheduled = false
      if (!has) return
      has = false
      apply(pending as T)
    })
  }
}

/**
 * Bounds covering the last ``days`` distinct IST trading dates of a candle
 * set (1m bars → each trading date spans many bars; dates are grouped by IST
 * wall date so a 00:00 IST bar isn't misattributed to the previous UTC day).
 * ``from`` is the first bar of the first kept date, ``to`` the last bar. Empty
 * input → ``null``; fewer dates than ``days`` → the whole range.
 */
export function lastNDays(candles: Candle[], days: number): { from: number; to: number } | null {
  if (candles.length === 0 || days <= 0) return null
  const dates: string[] = []
  for (const c of candles) {
    const d = istDateKey(c.time)
    if (dates[dates.length - 1] !== d) dates.push(d)
  }
  const firstKept = dates[Math.max(0, dates.length - days)]
  const i0 = candles.findIndex((c) => istDateKey(c.time) >= firstKept)
  return { from: candles[Math.max(0, i0)].time, to: candles[candles.length - 1].time }
}

/**
 * Slice candles to a replay window (inclusive on both ends, wire UTC epochs).
 * ``null`` returns the input unchanged — replaying the full history is the
 * no-op default. A window covering no bars returns ``[]`` (empty replay).
 */
export function windowBars(
  candles: Candle[],
  window: { from: number; to: number } | null,
): Candle[] {
  if (!window || candles.length === 0) return candles
  let i0 = candles.findIndex((c) => c.time >= window.from)
  if (i0 < 0) i0 = candles.length // window starts after the last bar
  let i1 = candles.length - 1
  while (i1 >= 0 && candles[i1].time > window.to) i1--
  if (i0 > i1) return []
  return candles.slice(i0, i1 + 1)
}

/** Last N IST trading dates from `candles` (matches chart wire window). */
export function clipChartDays(candles: Candle[], days: number): Candle[] {
  const w = lastNDays(candles, days)
  return w ? windowBars(candles, w) : candles
}

export interface InProgressBar {
  bar: Candle
  /** Price of the last revealed tick (null when the bar has no ticks). */
  tickPrice: number | null
  /** Ticks revealed inside the bar (1..steps). */
  revealed: number
  steps: number
}

/**
 * The in-progress bar at a tick cursor: the real bar plus the current
 * synthesized tick price and intra-bar progress. ``null`` at cursor 0 / empty
 * dataset. Same cursor math as :func:`visibleBars` — one source of truth.
 */
export function inProgressBar(
  candles: Candle[],
  cursor: number,
  stepsPerBar: number,
  ticksByBar: TickBarMap,
): InProgressBar | null {
  if (cursor <= 0 || candles.length === 0) return null
  const steps = Math.max(1, stepsPerBar)
  const barIndex = Math.min(Math.floor((cursor - 1) / steps), candles.length - 1)
  const revealed = cursor - barIndex * steps
  const bar = candles[barIndex]
  const barTicks = ticksByBar.get(bar.time)
  const tickPrice = barTicks && barTicks.prices.length > 0
    ? barTicks.prices[Math.min(revealed - 1, barTicks.prices.length - 1)]
    : null
  return { bar, tickPrice, revealed, steps }
}

/**
 * Bars revealed at a tick cursor, with the in-progress bar animated from its
 * ticks. Pure — unit-tested.
 *
 * - cursor 0 → no bars.
 * - Bar ``floor((cursor-1)/stepsPerBar)`` is the in-progress bar; its OHLCV
 *   is derived from the ticks revealed so far (open = bar open, close = last
 *   tick, high/low = extremes touched, volume = partial sum).
 * - A bar with no ticks (older bars beyond the tick budget) renders as its
 *   real candle — graceful fallback, no client-side interval branching.
 */
export function visibleBars(
  candles: Candle[],
  cursor: number,
  stepsPerBar: number,
  ticksByBar: TickBarMap,
): Candle[] {
  if (cursor <= 0 || candles.length === 0) return []
  const steps = Math.max(1, stepsPerBar)
  const barIndex = Math.min(Math.floor((cursor - 1) / steps), candles.length - 1)
  const revealed = cursor - barIndex * steps // ticks revealed inside the in-progress bar
  const base = candles.slice(0, barIndex + 1)
  // A fully-revealed bar is the real candle (its last tick equals close,
  // extremes and volume converge — true even for stepsPerBar 1).
  if (revealed >= steps) return base
  const k = revealed - 1
  const barTicks = ticksByBar.get(candles[barIndex].time)
  if (!barTicks || k >= barTicks.prices.length) return base

  const bar = candles[barIndex]
  let high = bar.open
  let low = bar.open
  let volume = 0
  for (let i = 0; i <= k; i++) {
    const p = barTicks.prices[i]
    if (p > high) high = p
    if (p < low) low = p
    volume += barTicks.quantities[i]
  }
  base[base.length - 1] = { ...bar, high, low, close: barTicks.prices[k], volume }
  return base
}
