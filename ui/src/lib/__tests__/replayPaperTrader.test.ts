import { describe, expect, it } from 'vitest'
import {
  INITIAL_REPLAY_CAPITAL,
  simulateReplayPaperTrades,
} from '../replayPaperTrader'
import type { Candle, HalfTrendMarker } from '../../types/market'

describe('simulateReplayPaperTrades', () => {
  it('returns default 1M initial capital when no candles or markers are provided', () => {
    const res = simulateReplayPaperTrades([], [], 'NIFTY')
    expect(res.initial_capital).toBe(INITIAL_REPLAY_CAPITAL)
    expect(res.balance).toBe(1_000_000)
    expect(res.equity).toBe(1_000_000)
    expect(res.open_position).toBeNull()
    expect(res.trades).toEqual([])
  })

  it('opens a LONG position on BUY marker and closes on Take Profit', () => {
    const candles: Candle[] = [
      { time: 1000, open: 100, high: 105, low: 99, close: 102, volume: 100 },
      { time: 2000, open: 102, high: 115, low: 101, close: 114, volume: 120 },
      { time: 3000, open: 114, high: 125, low: 113, close: 120, volume: 150 },
    ]
    const markers: HalfTrendMarker[] = [
      {
        index: 0,
        time: 1000,
        side: 'BUY',
        price: 102,
        ht: 98,
        trend: 0,
      },
    ]

    const res = simulateReplayPaperTrades(candles, markers, 'NIFTY', 10)
    // Risk = 102 - 98 = 4, TP = 102 + 8 = 110, SL = 98
    // On candle 2000, high is 115 >= 110 (TP hit!)
    expect(res.trades.length).toBe(1)
    expect(res.trades[0].exit_reason).toBe('Take Profit (TP)')
    expect(res.trades[0].exit_price).toBe(110)
    expect(res.trades[0].pnl).toBe((110 - 102) * 10) // 80
    expect(res.balance).toBe(1_000_000 + 80)
    expect(res.equity).toBe(1_000_000 + 80)
  })

  it('tracks open floating PnL when position is currently active', () => {
    const candles: Candle[] = [
      { time: 1000, open: 100, high: 102, low: 98, close: 100, volume: 100 },
      { time: 2000, open: 100, high: 104, low: 99, close: 103, volume: 120 },
    ]
    const markers: HalfTrendMarker[] = [
      {
        index: 0,
        time: 1000,
        side: 'BUY',
        price: 100,
        ht: 95,
        trend: 0,
      },
    ]

    const res = simulateReplayPaperTrades(candles, markers, 'NIFTY', 50)
    expect(res.trades.length).toBe(0)
    expect(res.open_position).not.toBeNull()
    expect(res.open_position?.side).toBe('BUY')
    expect(res.open_position?.unrealized_pnl).toBe((103 - 100) * 50) // 150
    expect(res.equity).toBe(1_000_000 + 150)
  })
})
