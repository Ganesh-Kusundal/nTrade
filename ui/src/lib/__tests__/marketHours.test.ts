import { describe, expect, it } from 'vitest'
import { strategySession } from '../marketHours'
import { runValentini } from '../valentini'
import type { Candle } from '../../types/market'

describe('strategySession', () => {
  it('uses NSE window by default and MCX evening close for MCX', () => {
    expect(strategySession()).toEqual({ start: '09:15', end: '15:25' })
    expect(strategySession('NFO')).toEqual({ start: '09:15', end: '15:25' })
    expect(strategySession('MCX')).toEqual({ start: '09:00', end: '23:25' })
  })

  it('maps SILVERM / commodity roots to MCX even when exchange is missing', () => {
    // Regression: SILVERM chart was painting END at ~15:30 (NSE hard-close)
    // because a missing/stale exchange tag fell through to the NSE window.
    expect(strategySession(undefined, 'SILVERM')).toEqual({ start: '09:00', end: '23:25' })
    expect(strategySession('NFO', 'SILVERM')).toEqual({ start: '09:00', end: '23:25' })
    expect(strategySession(null, 'SILVERM AUG FUT')).toEqual({ start: '09:00', end: '23:25' })
    expect(strategySession(undefined, 'GOLDM')).toEqual({ start: '09:00', end: '23:25' })
    expect(strategySession(undefined, 'NIFTY')).toEqual({ start: '09:15', end: '15:25' })
  })
})

describe('MCX session_close marker', () => {
  /** 2026-08-10 10:00 IST → UTC epoch seconds. */
  const T0 = Date.UTC(2026, 7, 10, 4, 30) / 1000

  function candle(i: number, opts: Partial<Candle> & { ts?: number } = {}): Candle {
    const c = opts.close ?? 100 + i * 0.5
    return {
      time: opts.ts ?? T0 + i * 60,
      open: opts.open ?? c - 0.5,
      high: opts.high ?? c + 0.5,
      low: opts.low ?? c - 0.5,
      close: c,
      volume: opts.volume ?? 100,
    }
  }
  const absorption = (i: number, at: number, ts?: number) =>
    candle(i, { close: at, open: at - 0.02, high: at + 0.05, low: at - 0.05, volume: 1500, ts })

  it('does not force END at 15:30 on MCX — session runs to 23:25', () => {
    // Entry mid-morning, hold through NSE close into evening — still open.
    // Fixed rangeSize + a profile-shaped session so the new value-edge/POC
    // gates still fire the entry; the test only asserts the end-marker behaviour.
    const RANGE = 4.0
    const base = [
      ...Array.from({ length: 10 }, (_, k) => candle(k, { close: 112, open: 111.9, high: 112.4, low: 111.6, volume: 500 })),
      ...Array.from({ length: 3 }, (_, k) => candle(10 + k, { close: 108, open: 107.9, high: 108.4, low: 107.6 })),
      ...Array.from({ length: 3 }, (_, k) => candle(13 + k, { close: 116, open: 115.9, high: 116.4, low: 115.6 })),
      ...Array.from({ length: 4 }, (_, k) => candle(16 + k, { close: 112, open: 111.9, high: 112.4, low: 111.6, volume: 500 })),
    ]
    const afterNse = Date.UTC(2026, 7, 10, 10, 5) / 1000 // 15:35 IST
    const evening = Date.UTC(2026, 7, 10, 13, 0) / 1000 // 18:30 IST
    const bars = [
      ...base,
      absorption(20, 108),                         // morning (in session)
      candle(21, { close: 112, volume: 1000 }),
      candle(22, { close: 120, volume: 1000 }),     // entry — still in session
      candle(23, { close: 120.2, ts: afterNse }),  // 15:35 IST — past NSE close
      candle(24, { close: 120.4, ts: evening }),   // 18:30 IST
    ]
    const opts = { rangeSize: RANGE, warmup: 15, tpMultiplier: 2.0, minRr: 1.5, fadeExtended: false }
    const nse = runValentini(bars, { ...opts, sessionEnd: '15:25' })
    expect(nse.trades[0]?.reason).toBe('session_close')
    expect(nse.trades[0]?.exitIndex).toBe(23)

    // Auction-trail divergence (new high on weak volume) would fire here and
    // close the trade before the session end — this test is about the MCX
    // session marker, not the divergence exit, so opt out of the gate.
    const mcx = runValentini(bars, {
      ...opts,
      divergenceVolumeMult: 0,
      sessionStart: strategySession(undefined, 'SILVERM').start,
      sessionEnd: strategySession(undefined, 'SILVERM').end,
    })
    expect(mcx.trades[0]?.exitIndex).toBeNull()
    expect(mcx.trades[0]?.reason).toBeNull()
  })
})
