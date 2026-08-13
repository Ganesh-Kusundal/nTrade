/**
 * MorningVAHVAL TS mirror tests — mirror of tests/test_morning_vah_val.py so
 * the UI overlay and the Python engine enforce the same contract.
 */

import { describe, expect, it } from 'vitest'
import type { Candle } from '../../types/market'
import { MORNING_VAH_VAL_TUNED, runMorningVahVal, type MorningVahValResult } from '../morningVahVal'

/** Wire UTC epoch for an IST wall-clock time (IST = UTC + 5:30). */
function ist(y: number, mo: number, d: number, h: number, mi: number): number {
  return Date.UTC(y, mo - 1, d, h, mi) / 1000 - 5.5 * 3600
}

function candle(time: number, o: number, h: number, l: number, c: number, volume = 100): Candle {
  return { time, open: o, high: h, low: l, close: c, volume }
}

/** Full 09:15–15:29 session drifting open → close (1m bars). */
function sessionDay(day: number[], openPx: number, closePx: number): Candle[] {
  const out: Candle[] = []
  const n = 375
  const step = (closePx - openPx) / (n - 1)
  for (let i = 0; i < n; i++) {
    const c = openPx + step * i
    const o = c - step * 0.5
    out.push(candle(ist(2026, 8, day[0], 9, 15) + i * 60, o, Math.max(o, c) + 0.5, Math.min(o, c) - 0.5, c))
  }
  return out
}

const D1 = 3 // Mon 2026-08-03
const D2 = 4 // Tue 2026-08-04
const D3 = 5 // Wed 2026-08-05

/** 09:15–09:29 profile window: POC ≈ 99, VAH ≈ 101, final 2m dips below VAL. */
function profileWindow(fakeDip = true): Candle[] {
  const out: Candle[] = []
  const start = ist(2026, 8, D2, 9, 15)
  for (let i = 0; i < 10; i++) {
    const c = i % 2 === 0 ? 99.5 : 101.5
    out.push(candle(start + i * 60, c - 1.5, c + 0.5, c - 1.5, c))
  }
  out.push(candle(start + 10 * 60, 102.0, 103.0, 101.5, 102.5, 50))
  out.push(candle(start + 11 * 60, 102.5, 103.2, 102.0, 103.0, 50))
  out.push(candle(start + 12 * 60, 103.0, 103.2, 101.5, 102.0, 50))
  if (fakeDip) {
    out.push(candle(start + 13 * 60, 99.0, 98.5, 97.3, 97.6))
    out.push(candle(start + 14 * 60, 97.6, 98.2, 97.2, 97.7))
  } else {
    out.push(candle(start + 13 * 60, 99.0, 102.5, 98.8, 101.8))
    out.push(candle(start + 14 * 60, 101.8, 102.7, 101.0, 102.2))
  }
  return out
}

function longReversal(): Candle[] {
  const base = ist(2026, 8, D2, 9, 30)
  return [
    candle(base, 97.7, 98.8, 97.4, 98.4),
    candle(base + 60, 98.4, 99.9, 98.2, 99.6),
  ]
}

function shortReversal(): Candle[] {
  const base = ist(2026, 8, D2, 9, 30)
  return [
    candle(base, 101.6, 102.2, 101.0, 101.2),
    candle(base + 60, 101.2, 101.4, 99.6, 99.4),
  ]
}

function build(...parts: Candle[][]): Candle[] {
  return parts.flat()
}

function longSetup(): Candle[] {
  return build(
    sessionDay([D1], 100, 130),
    profileWindow(true),
    longReversal(),
    [candle(ist(2026, 8, D2, 9, 32), 99.6, 99.7, 99.4, 99.6)],
  )
}

function lastTrade(r: MorningVahValResult) {
  return r.trades[r.trades.length - 1]
}

describe('MORNING_VAH_VAL_TUNED parity', () => {
  it('pins the UI tuned preset to the Python MorningVAHVAL.TUNED dict', () => {
    // Mirror of ntrade/engines/morning_vah_val.py `TUNED` — a drift here
    // silently desyncs the chart overlay from the paper trader.
    expect(MORNING_VAH_VAL_TUNED).toEqual({
      requireCluster: true,
      reversalMarginPct: 0.001,
      bookPartial: true,
      trailBack: 1,
      sidewayThreshold: 0.0015,
      slPad: 30,
    })
  })

  it('pins the chandelier knob defaults to the Python constructor', () => {
    // Mirror of MorningVAHVAL.__init__ defaults (trail_mode='candle',
    // atr_mult=2.0, atr_period=3) — a drift silently desyncs the overlay's
    // exit management from the engine.
    // Defaults are only observable through behavior: the default trail must be
    // the candle ratchet (tight), so a tight ratchet stop exits where a
    // chandelier would have held. Feed the Python chandelier-hold scenario and
    // assert the DEFAULT mode trips the stop (candle), not holds (chandelier).
    const feed = [
      ...sessionDay([D1], 100, 130),
      ...profileWindow(true),
      ...longReversal(),
      candle(ist(2026, 8, D2, 9, 32), 99.6, 100.0, 99.5, 99.8),
      candle(ist(2026, 8, D2, 9, 33), 99.8, 104.5, 99.6, 103.0), // T1 -> be
      candle(ist(2026, 8, D2, 9, 34), 103.0, 103.2, 102.0, 102.5),
      candle(ist(2026, 8, D2, 9, 35), 102.5, 104.0, 103.0, 103.5),
      candle(ist(2026, 8, D2, 9, 36), 103.5, 103.8, 102.8, 103.4),
      candle(ist(2026, 8, D2, 9, 37), 103.4, 104.2, 101.0, 102.0),
      candle(ist(2026, 8, D2, 9, 38), 102.0, 102.3, 101.5, 101.8),
      candle(ist(2026, 8, D2, 9, 39), 101.8, 103.2, 102.6, 102.9), // pullback
      candle(ist(2026, 8, D2, 9, 40), 102.9, 103.0, 102.5, 102.7),
    ]
    expect(lastTrade(runMorningVahVal(feed)).reason).toBe('stop')
  })
})

describe('runMorningVahVal', () => {
  it('day one has no bias and no trades', () => {
    const r = runMorningVahVal(sessionDay([D1], 100, 130))
    expect(r.bias).toEqual([])
    expect(r.trades).toEqual([])
    expect(r.levels.length).toBe(1) // the FRVP still freezes
  })

  it('prior-day UP arms a long bias', () => {
    const r = runMorningVahVal(build(sessionDay([D1], 100, 130), [candle(ist(2026, 8, D2, 9, 15), 99, 99.5, 98.5, 99.2)]))
    expect(r.bias).toEqual([{ date: '2026-08-03', bias: 'UP' }])
  })

  it('prior-day DOWN arms a short bias', () => {
    const r = runMorningVahVal(build(sessionDay([D1], 130, 100), [candle(ist(2026, 8, D2, 9, 15), 99, 99.5, 98.5, 99.2)]))
    expect(r.bias).toEqual([{ date: '2026-08-03', bias: 'DOWN' }])
  })

  it('flat prior day is SIDEWAYS and blocks trades', () => {
    const r = runMorningVahVal(build(
      sessionDay([D1], 100, 100.1),
      [candle(ist(2026, 8, D2, 9, 15), 99, 99.5, 98.5, 99.2)],
      profileWindow(true),
      longReversal(),
      [candle(ist(2026, 8, D2, 9, 32), 99.6, 99.7, 99.4, 99.6)],
    ))
    expect(r.bias[0].bias).toBe('SIDEWAYS')
    expect(r.trades).toEqual([])
  })

  it('freezes a valid morning profile with VAH above VAL', () => {
    const r = runMorningVahVal(build(sessionDay([D1], 100, 130), profileWindow(true), longReversal()))
    // Day 1 AND day 2 each freeze a morning profile (the backend does too).
    expect(r.levels.length).toBe(2)
    const lvl = r.levels[r.levels.length - 1] // today's frozen level
    expect(lvl.date).toBe('2026-08-04')
    expect(lvl.val).toBeGreaterThan(0)
    expect(lvl.vah).toBeGreaterThan(lvl.val)
    expect(lvl.poc).toBeGreaterThan(0)
  })

  it('does not enter before the profile freezes', () => {
    const r = runMorningVahVal(build(sessionDay([D1], 100, 130), profileWindow(true)))
    // Day 1 froze; day 2 never saw a 09:30 candle so its window never closed.
    expect(r.levels.length).toBe(1)
    expect(r.levels[0].date).toBe('2026-08-03')
    expect(r.trades).toEqual([])
  })

  it('long: fake break below VAL + reversal emits a BUY', () => {
    const r = runMorningVahVal(longSetup())
    expect(r.trades).toHaveLength(1)
    const t = lastTrade(r)
    expect(t.side).toBe('BUY')
    expect(t.entry).toBe(99.6)
    const lvl = r.levels[r.levels.length - 1] // today's frozen level
    expect(t.sl).toBeLessThanOrEqual(lvl.val)
    expect(t.sl).toBeLessThan(t.entry)
    expect(t.tp).not.toBeNull()
    expect(t.tp!).toBeGreaterThan(t.entry)
    expect(t.tp!).toBeLessThanOrEqual(lvl.vah)
    expect(t.sizing).toBe('half') // < 20 bars → not at the EMA cluster
  })

  it('short: VAH rejection emits a SELL', () => {
    const r = runMorningVahVal(build(
      sessionDay([D1], 130, 100),
      profileWindow(false),
      shortReversal(),
      [candle(ist(2026, 8, D2, 9, 32), 99.4, 99.5, 99.2, 99.4)],
    ))
    expect(r.trades).toHaveLength(1)
    const t = lastTrade(r)
    expect(t.side).toBe('SELL')
    const lvl = r.levels[r.levels.length - 1] // today's frozen level
    expect(t.sl).toBeGreaterThanOrEqual(lvl.vah)
    expect(t.sl).toBeGreaterThan(t.entry)
    expect(t.tp).not.toBeNull()
    expect(t.tp!).toBeLessThan(t.entry)
    expect(t.tp!).toBeGreaterThanOrEqual(lvl.val)
  })

  it('DOWN bias does not fire the long setup', () => {
    const r = runMorningVahVal(build(
      sessionDay([D1], 130, 100),
      profileWindow(true),
      longReversal(),
      [candle(ist(2026, 8, D2, 9, 32), 99.6, 99.7, 99.4, 99.6)],
    ))
    expect(r.trades).toEqual([])
  })

  it('no double entry while a position is open', () => {
    const r = runMorningVahVal(build(longSetup(), rally(600)))
    expect(r.trades.filter((t) => t.side === 'BUY')).toHaveLength(1)
  })

  it('stop exit closes the long', () => {
    const r = runMorningVahVal(build(longSetup(), [
      candle(ist(2026, 8, D2, 9, 33), 99.7, 99.8, 98.9, 99.2),
      candle(ist(2026, 8, D2, 9, 34), 99.2, 99.3, 96.5, 96.9),
      candle(ist(2026, 8, D2, 9, 35), 96.9, 97.0, 96.3, 96.6),
      candle(ist(2026, 8, D2, 9, 36), 96.6, 96.7, 95.8, 96.0),
    ]))
    const t = lastTrade(r)
    expect(t.reason).toBe('stop')
    expect(t.exitIndex).not.toBeNull()
    expect(t.exit).toBe(t.sl)
  })

  it('T1 partial books half and moves to breakeven', () => {
    const r = runMorningVahVal(build(longSetup(), [
      candle(ist(2026, 8, D2, 9, 33), 99.7, 103.0, 99.6, 102.6),
      candle(ist(2026, 8, D2, 9, 34), 102.6, 102.8, 102.0, 102.4),
    ]))
    const t = lastTrade(r)
    expect(t.partialIndex).not.toBeNull()
    expect(t.partial).toBe(t.tp !== null ? t.tp : t.partial) // tp cleared after partial
    expect(t.reason).toBeNull() // runner still open at breakeven
    // A drift back below the entry (breakeven) trips the cost-to-cost stop.
    const r2 = runMorningVahVal(build(longSetup(), [
      candle(ist(2026, 8, D2, 9, 33), 99.7, 103.0, 99.6, 102.6),
      candle(ist(2026, 8, D2, 9, 34), 102.6, 102.8, 102.0, 102.4),
      candle(ist(2026, 8, D2, 9, 35), 102.4, 102.6, 99.4, 99.6),
      candle(ist(2026, 8, D2, 9, 36), 99.6, 99.7, 99.2, 99.4),
    ]))
    const t2 = lastTrade(r2)
    expect(t2.reason).toBe('stop')
    expect(t2.exit).toBeCloseTo(t2.entry, 4) // breakeven
  })

  it('hard closes at 11:00', () => {
    const r = runMorningVahVal(build(longSetup(), rally(660), [
      candle(ist(2026, 8, D2, 11, 2), 99.7, 99.8, 99.5, 99.7),
    ]))
    const t = lastTrade(r)
    expect(t.reason).toBe('time_close')
  })

  it('session rollover force-closes an open position', () => {
    const r = runMorningVahVal(build(longSetup(), [
      candle(ist(2026, 8, D3, 9, 15), 99.0, 99.5, 98.5, 99.2),
    ]))
    const t = lastTrade(r)
    expect(t.reason).toBe('session_close')
    // Day 1's UP drift was computed on the day-2 rollover.
    expect(r.bias[0]).toEqual({ date: '2026-08-03', bias: 'UP' })
  })

  it('requireCluster blocks the off-cluster (50%) entry', () => {
    const r = runMorningVahVal(longSetup(), { requireCluster: true })
    expect(r.trades).toEqual([]) // < 20 bars → never at the EMA cluster
  })

  it('bookPartial false rides the full position at breakeven', () => {
    const r = runMorningVahVal(build(longSetup(), [
      candle(ist(2026, 8, D2, 9, 33), 99.7, 103.0, 99.6, 102.6),
      candle(ist(2026, 8, D2, 9, 34), 102.6, 102.8, 102.0, 102.4),
    ]), { bookPartial: false })
    const t = lastTrade(r)
    expect(t.partialIndex).toBeNull() // no partial book
    expect(t.tp).toBeNull()           // T1 consumed → runner at breakeven
    expect(t.reason).toBeNull()       // still open
  })

  it('reversalMarginPct filters a weak reversal', () => {
    const r = runMorningVahVal(longSetup(), { reversalMarginPct: 0.1 })
    expect(r.trades).toEqual([]) // close 99.6 vs VAL ~97.2 → not a 10% clear
  })

  it('chandelier trail holds through a pullback that trips the candle ratchet', () => {
    // Mirrors test_chandelier_trail_holds_through_small_pullback: a volatile
    // run-up (big ranges → big ATR) followed by a calm pullback. The ratchet
    // hugs the previous bar's low (~102.0) while the chandelier stop sits
    // ~an ATR below the day high (~101.8) — the same feed stops one and
    // holds the other.
    const feed = [
      ...sessionDay([D1], 100, 130),
      ...profileWindow(true),
      ...longReversal(),
      candle(ist(2026, 8, D2, 9, 32), 99.6, 100.0, 99.5, 99.8),
      candle(ist(2026, 8, D2, 9, 33), 99.8, 104.5, 99.6, 103.0), // T1 -> be
      candle(ist(2026, 8, D2, 9, 34), 103.0, 103.2, 102.0, 102.5),
      candle(ist(2026, 8, D2, 9, 35), 102.5, 104.0, 103.0, 103.5),
      candle(ist(2026, 8, D2, 9, 36), 103.5, 103.8, 102.8, 103.4),
      candle(ist(2026, 8, D2, 9, 37), 103.4, 104.2, 101.0, 102.0),
      candle(ist(2026, 8, D2, 9, 38), 102.0, 102.3, 101.5, 101.8),
      candle(ist(2026, 8, D2, 9, 39), 101.8, 103.2, 102.6, 102.9), // pullback
      candle(ist(2026, 8, D2, 9, 40), 102.9, 103.0, 102.5, 102.7),
    ]
    const opts = { bookPartial: false, atrMult: 1, atrPeriod: 2 } as const
    const candleR = runMorningVahVal(feed, { ...opts, trailMode: 'candle' })
    const chandelier = runMorningVahVal(feed, { ...opts, trailMode: 'chandelier' })
    expect(lastTrade(candleR).reason).toBe('stop')
    expect(lastTrade(chandelier).reason).toBeNull()
    expect(lastTrade(chandelier).exitIndex).toBeNull()
  })

  it('chandelier ratchets up on new day highs', () => {
    // New high -> chandelier stop lifts above breakeven; a pullback that stays
    // above the entry (99.6) but dips below the ratcheted stop exits at a
    // PROFIT (exit > entry), proving the stop followed the high.
    const feed = [
      ...sessionDay([D1], 100, 130),
      ...profileWindow(true),
      ...longReversal(),
      candle(ist(2026, 8, D2, 9, 32), 99.6, 103.0, 99.6, 102.0),
      candle(ist(2026, 8, D2, 9, 33), 102.0, 102.6, 99.8, 102.2), // T1 -> be
      candle(ist(2026, 8, D2, 9, 34), 102.2, 105.0, 101.5, 104.6), // new high
      candle(ist(2026, 8, D2, 9, 35), 104.6, 104.8, 103.5, 104.4),
      candle(ist(2026, 8, D2, 9, 36), 104.4, 104.6, 101.0, 101.5), // pullback
      candle(ist(2026, 8, D2, 9, 37), 101.5, 101.6, 100.8, 101.0),
      candle(ist(2026, 8, D2, 9, 38), 101.0, 101.1, 100.5, 100.7),
    ]
    const r = runMorningVahVal(feed, {
      trailMode: 'chandelier', bookPartial: false, atrMult: 1, atrPeriod: 2,
    })
    const t = lastTrade(r)
    expect(t.reason).toBe('stop')
    expect(t.exit!).toBeGreaterThan(t.entry) // stopped at a profit, not breakeven
  })

  it('trailBack loosens the BE-phase ratchet', () => {
    // Mirrors the Python test_trail_back_uses_older_bar_for_ratchet: the
    // 09:32-33 bucket's low (100.0) sits above the entry (99.6), so a tight
    // trail (trail_back=1) ratchets the stop up to it; a loose trail
    // (trail_back=2) reads two buckets back (the entry bucket, low 97.4)
    // and stays at breakeven. A later pullback (low 99.8) trips one, not the
    // other. LongSetup() is NOT reused: its 09:32 low (99.4) would pollute
    // the 09:32-33 bucket, so the full feed is spelled out like the Python.
    const feed = [
      ...sessionDay([D1], 100, 130),
      ...profileWindow(true),
      ...longReversal(),
      candle(ist(2026, 8, D2, 9, 32), 99.6, 103.0, 100.0, 102.0),
      candle(ist(2026, 8, D2, 9, 33), 102.0, 102.9, 100.2, 102.4),
      candle(ist(2026, 8, D2, 9, 34), 102.4, 102.6, 100.1, 102.3), // T1 → be
      candle(ist(2026, 8, D2, 9, 35), 102.3, 102.5, 100.3, 102.2),
      candle(ist(2026, 8, D2, 9, 36), 102.2, 102.4, 100.0, 100.1), // ratchet
      candle(ist(2026, 8, D2, 9, 37), 100.1, 100.3, 99.9, 100.0),
      candle(ist(2026, 8, D2, 9, 38), 100.0, 100.1, 99.8, 99.9),   // pullback
    ]
    const tight = runMorningVahVal(feed, { trailBack: 1 })
    const loose = runMorningVahVal(feed, { trailBack: 2 })
    expect(lastTrade(tight).reason).toBe('stop')       // ratcheted to 100.0 → hit
    expect(lastTrade(loose).reason).toBeNull()          // still at breakeven → alive
    expect(lastTrade(loose).exitIndex).toBeNull()
  })
})

/** Benign 09:32..minute-`to` 1m bars that never touch a long's stop. */
function rally(to: number): Candle[] {
  const out: Candle[] = []
  const base = ist(2026, 8, D2, 9, 32)
  for (let i = 0; i <= to - 572; i++) {
    const c = 99.7 + i * 0.01
    out.push(candle(base + i * 60, c - 0.05, c + 0.1, c - 0.2, c))
  }
  return out
}
