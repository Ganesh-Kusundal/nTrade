import { describe, expect, it } from 'vitest'
import { feedKind, isSessionOpen, exchangeSession } from '../feedStatus'

describe('feedKind', () => {
  it('replay wins regardless of live/WS state', () => {
    expect(feedKind('replay', 'dhan', false, 'off', false)).toBe('replay')
    expect(feedKind('replay', 'dhan', true, 'connected', false)).toBe('replay')
  })

  it('non-live provider is historical if genuine store, else offline', () => {
    expect(feedKind('live', 'parquet', false, 'off', false)).toBe('historical')
    // a live provider whose socket was never opened (paper/seed) is not streaming
    expect(feedKind('live', 'dhan', true, 'off', false)).toBe('offline')
  })

  it('labels synthetic provider as synthetic, not historical', () => {
    expect(feedKind('live', 'synthetic', false, 'connected', false)).toBe('synthetic')
  })

  it('flags stale when WS is up but no ticks', () => {
    expect(feedKind('live', 'dhan', true, 'connected', true)).toBe('stale')
  })

  it('streams only for a live provider with connected+fresh socket', () => {
    expect(feedKind('live', 'dhan', true, 'connected', false)).toBe('streaming')
    expect(feedKind('live', 'dhan', true, 'reconnecting', false)).toBe('reconnecting')
    expect(feedKind('live', 'dhan', true, 'disconnected', false)).toBe('offline')
  })
})

describe('exchangeSession', () => {
  it('uses NSE/NFO hours unless MCX', () => {
    expect(exchangeSession()).toEqual({ start: '09:15', end: '15:30' })
    expect(exchangeSession('NFO')).toEqual({ start: '09:15', end: '15:30' })
  })

  it('maps MCX commodities to evening session, even if exchange tag is NFO/stale', () => {
    expect(exchangeSession('NFO', 'SILVERM')).toEqual({ start: '09:00', end: '23:30' })
    expect(exchangeSession(undefined, 'SILVERM AUG FUT')).toEqual({ start: '09:00', end: '23:30' })
  })

  it('distinguishes exchange open (15:30) from strategy close (15:25)', () => {
    // The OPEN/CLOSED chip must be WIDER than Fabio's 15:25 hard close.
    expect(exchangeSession().end).toBe('15:30')
    expect(exchangeSession('MCX').end).toBe('23:30')
  })
})

describe('isSessionOpen', () => {
  // 2026-08-10 is a Monday in IST. IST = UTC + 5:30, so build UTC epochs.
  const monday0930 = Date.UTC(2026, 7, 10, 4, 0) / 1000 // 09:30 IST
  const monday1526 = Date.UTC(2026, 7, 10, 9, 56) / 1000 // 15:26 IST — inside session, past strategy close
  const monday1531 = Date.UTC(2026, 7, 10, 10, 1) / 1000 // 15:31 IST — closed
  const saturday = Date.UTC(2026, 7, 8, 3, 30) / 1000 // Saturday 09:00 IST

  it('NIFTY open mid-morning', () => {
    expect(isSessionOpen('NFO', 'NIFTY', monday0930)).toBe(true)
  })

  it('NIFTY open at 15:26 — proves OPEN is not the 15:25 strategy close', () => {
    expect(isSessionOpen('NFO', 'NIFTY', monday1526)).toBe(true)
  })

  it('NIFTY closed at 15:31', () => {
    expect(isSessionOpen('NFO', 'NIFTY', monday1531)).toBe(false)
  })

  it('closed on Saturday', () => {
    expect(isSessionOpen('NFO', 'NIFTY', saturday)).toBe(false)
  })

  it('MCX open at 18:30 IST (past NSE close, still in evening session)', () => {
    const monday1830 = Date.UTC(2026, 7, 10, 13, 0) / 1000
    expect(isSessionOpen('MCX', 'SILVERM', monday1830)).toBe(true)
  })
})
