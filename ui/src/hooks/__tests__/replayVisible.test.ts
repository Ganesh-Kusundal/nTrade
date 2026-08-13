import { describe, expect, it } from 'vitest'
import type { Candle, TickBar } from '../../types/market'
import { istInputToEpoch } from '../../lib/istTime'
import { closedBars, coalesceLatest, clipChartDays, inProgressBar, lastNDays, mergeByTime, mergeLive, visibleBars, windowBars, type TickBarMap } from '../replayVisible'

function bar(time: number, open: number, high: number, low: number, close: number, volume: number): Candle {
  return { time, open, high, low, close, volume }
}

/** Bar-faithful ticks: first = open, last = close, extremes touched, qty sums to volume. */
const candles = [
  bar(100, 10, 12, 9, 11, 100),
  bar(160, 11, 14, 10, 13, 200),
]

const ticksA: TickBar = {
  time: 100,
  prices: [10, 12, 9, 11],
  quantities: [25, 25, 25, 25],
}

const ticksB: TickBar = {
  time: 160,
  prices: [11, 14, 10, 13],
  quantities: [50, 50, 50, 50],
}

const both: TickBarMap = new Map<number, TickBar>([
  [100, ticksA],
  [160, ticksB],
])

describe('visibleBars', () => {
  it('cursor 0 reveals nothing', () => {
    expect(visibleBars(candles, 0, 4, both)).toEqual([])
  })

  it('mid-bar animates the in-progress bar from its ticks', () => {
    // cursor 3 → third tick of bar 0: close = 9, high = 12 (touched), low = 9, volume = 75
    const out = visibleBars(candles, 3, 4, both)
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ time: 100, open: 10, high: 12, low: 9, close: 9, volume: 75 })
  })

  it('a fully-ticked bar converges to the real candle', () => {
    const done = visibleBars(candles, 4, 4, both) // bar 0 complete
    expect(done).toHaveLength(1)
    expect(done[0]).toEqual(candles[0])
    const next = visibleBars(candles, 5, 4, both) // first tick of bar 1
    expect(next).toHaveLength(2)
    expect(next[0]).toEqual(candles[0])
    expect(next[1]).toMatchObject({ time: 160, open: 11, close: 11, volume: 50 })
  })

  it('stepsPerBar 1 is plain bar replay', () => {
    const out = visibleBars(candles, 2, 1, both)
    expect(out).toEqual([candles[0], candles[1]])
  })

  it('a bar with fewer ticks than needed falls back to its real candle', () => {
    // Bar 1 has only its first tick; cursor 62 wants tick 2 of bar 1 → fall back.
    const partial: TickBarMap = new Map<number, TickBar>([
      [160, { time: 160, prices: [11], quantities: [50] }],
    ])
    const out = visibleBars(candles, 62, 60, partial)
    expect(out[0]).toEqual(candles[0]) // real — no ticks for bar 0
    expect(out[1]).toEqual(candles[1]) // partial coverage → real bar
  })

  it('a bar with no ticks at all is shown as its real candle', () => {
    const out = visibleBars(candles, 61, 60, new Map()) // bar 1, first tick — no ticks anywhere
    expect(out).toEqual([candles[0], candles[1]])
  })

  it('seeking backward into an earlier bar re-animates it', () => {
    const out = visibleBars(candles, 2, 4, both) // bar 0, tick 2
    expect(out).toHaveLength(1)
    expect(out[0].close).toBe(12)
    expect(out[0].high).toBe(12)
    expect(out[0].volume).toBe(50)
  })
})

describe('windowBars', () => {
  const many = [
    bar(1000, 1, 1, 1, 1, 1),
    bar(2000, 1, 1, 1, 1, 1),
    bar(3000, 1, 1, 1, 1, 1),
    bar(4000, 1, 1, 1, 1, 1),
  ]

  it('null window returns the input unchanged (full history)', () => {
    expect(windowBars(many, null)).toBe(many)
  })

  it('slices an inclusive range', () => {
    expect(windowBars(many, { from: 1500, to: 3500 })).toEqual([many[1], many[2]])
    expect(windowBars(many, { from: 1000, to: 4000 })).toEqual(many)
  })

  it('window covering no bars yields an empty replay', () => {
    expect(windowBars(many, { from: 5000, to: 6000 })).toEqual([])
    expect(windowBars(many, { from: 100, to: 900 })).toEqual([])
  })

  it('handles an empty dataset', () => {
    expect(windowBars([], { from: 0, to: 9000 })).toEqual([])
    expect(windowBars([], null)).toEqual([])
  })
})

describe('lastNDays', () => {
  // IST wall times → wire epochs, so date grouping is deterministic.
  const d1 = istInputToEpoch('2026-08-04T09:15')!
  const d1e = istInputToEpoch('2026-08-04T15:30')!
  const d2 = istInputToEpoch('2026-08-05T09:15')!
  const d3 = istInputToEpoch('2026-08-06T09:15')!
  const d4 = istInputToEpoch('2026-08-07T09:15')!
  const mk = (t: number): Candle => bar(t, 1, 2, 0, 1, 10)

  it('keeps only the last 3 distinct trading dates', () => {
    // 4 distinct dates (d1e shares Aug 4 with d1) → keep Aug 5–7, from = first bar of Aug 5.
    const out = lastNDays([mk(d1), mk(d1e), mk(d2), mk(d3), mk(d4)], 3)
    expect(out).toEqual({ from: d2, to: d4 })
  })

  it('fewer dates than requested → the whole range', () => {
    expect(lastNDays([mk(d1), mk(d2)], 3)).toEqual({ from: d1, to: d2 })
  })

  it('handles an empty dataset', () => {
    expect(lastNDays([], 3)).toBeNull()
    expect(lastNDays([mk(d1)], 0)).toBeNull()
  })
})

describe('clipChartDays', () => {
  const d1 = istInputToEpoch('2026-08-04T09:15')!
  const d2 = istInputToEpoch('2026-08-05T09:15')!
  const d3 = istInputToEpoch('2026-08-06T09:15')!
  const d4 = istInputToEpoch('2026-08-07T09:15')!
  const mk = (t: number): Candle => bar(t, 1, 2, 0, 1, 10)

  it('drops bars before the last N IST trading dates', () => {
    const all = [mk(d1), mk(d2), mk(d3), mk(d4)]
    expect(clipChartDays(all, 3)).toEqual([mk(d2), mk(d3), mk(d4)])
  })
})

describe('inProgressBar', () => {
  it('reports the real bar, current tick price and progress mid-bar', () => {
    const p = inProgressBar(candles, 3, 4, both) // bar 0, 3rd tick (price 9)
    expect(p).not.toBeNull()
    expect(p!.bar).toEqual(candles[0])
    expect(p!.tickPrice).toBe(9)
    expect(p!.revealed).toBe(3)
    expect(p!.steps).toBe(4)
  })

  it('converges to the bar close on the last tick', () => {
    const p = inProgressBar(candles, 4, 4, both)
    expect(p!.tickPrice).toBe(candles[0].close)
    expect(p!.revealed).toBe(4)
  })

  it('bar without ticks yields a null tick price (bar replay)', () => {
    const p = inProgressBar(candles, 2, 1, new Map())
    expect(p).not.toBeNull()
    expect(p!.tickPrice).toBeNull()
    expect(p!.bar).toEqual(candles[1])
  })

  it('cursor 0 has no in-progress bar', () => {
    expect(inProgressBar(candles, 0, 4, both)).toBeNull()
  })
})

describe('mergeLive', () => {
  it('revises the last bar in place when the time matches (the live path)', () => {
    const next = mergeLive(candles, { ...candles[1], close: 99, volume: 9 })
    expect(next).toHaveLength(2)
    expect(next[0]).toBe(candles[0])
    expect(next[1]).toMatchObject({ time: 160, close: 99, volume: 9 })
  })

  it('appends a newer bar without sorting the prefix', () => {
    const next = mergeLive(candles, bar(220, 13, 14, 12, 13, 10))
    expect(next.map((c) => c.time)).toEqual([100, 160, 220])
  })
})

describe('mergeByTime', () => {
  it('returns history unchanged when there are no live bars', () => {
    expect(mergeByTime(candles, [])).toBe(candles)
  })

  it('live wins on the same timestamp', () => {
    const live = [{ ...candles[1], close: 50 }]
    expect(mergeByTime(candles, live)[1].close).toBe(50)
  })
})

describe('closedBars', () => {
  it('drops the in-progress last bar so the strategy only sees closes', () => {
    expect(closedBars([])).toEqual([])
    expect(closedBars([candles[0]])).toEqual([])
    expect(closedBars(candles)).toEqual([candles[0]])
  })
})

describe('coalesceLatest', () => {
  it('keeps only the last value until the next macrotask', async () => {
    const got: number[] = []
    const push = coalesceLatest<number>((v) => got.push(v))
    push(1)
    push(2)
    push(3)
    expect(got).toEqual([])
    await new Promise((r) => setTimeout(r, 0))
    expect(got).toEqual([3])
  })
})
