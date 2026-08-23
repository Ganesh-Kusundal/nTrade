import type { Candle, HalfTrendMarker } from '../types/market'

export interface ReplayPosition {
  id: string
  symbol: string
  side: 'BUY' | 'SELL'
  qty: number
  entry_price: number
  entry_time: number
  sl: number | null
  tp: number | null
  current_price: number
  unrealized_pnl: number
  return_pct: number
}

export interface ReplayTrade {
  id: string
  symbol: string
  side: 'BUY' | 'SELL'
  qty: number
  entry_price: number
  entry_time: number
  exit_price: number
  exit_time: number
  pnl: number
  return_pct: number
  exit_reason: 'Take Profit (TP)' | 'Stop Loss (SL)' | 'Reversal (Signal Flip)' | 'Manual Flatten'
}

export interface ReplayPaperAccount {
  initial_capital: number
  balance: number
  equity: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  return_pct: number
  n_trades: number
  win_trades: number
  loss_trades: number
  win_rate: number
  open_position: ReplayPosition | null
  trades: ReplayTrade[]
}

export const INITIAL_REPLAY_CAPITAL = 1_000_000 // ₹1,000,000 (1M INR)

/**
 * Simulates TradingView-style auto-execution of strategy signals in Bar Replay
 * mode against ₹1,000,000 paper capital.
 *
 * Rules:
 * 1. Capital = ₹1,000,000.
 * 2. On BUY signal: open LONG position at signal price with SL (ATR/channel) and TP (1:2 RR).
 * 3. On SELL signal: open SHORT position at signal price with SL (ATR/channel) and TP (1:2 RR).
 * 4. While holding position, check intra-bar High/Low of subsequent candles for SL/TP hits.
 * 5. If an opposite signal fires before SL/TP, auto-reverse (close old at open/signal price and open new).
 * 6. Computes live unrealized P&L on open position against the current bar's close.
 */
export function simulateReplayPaperTrades(
  candles: Candle[],
  markers: HalfTrendMarker[] | undefined | null,
  symbol: string,
  lotSize = 1,
  initialCapital = INITIAL_REPLAY_CAPITAL,
): ReplayPaperAccount {
  if (candles.length === 0 || !markers || markers.length === 0) {
    return {
      initial_capital: initialCapital,
      balance: initialCapital,
      equity: initialCapital,
      realized_pnl: 0,
      unrealized_pnl: 0,
      total_pnl: 0,
      return_pct: 0,
      n_trades: 0,
      win_trades: 0,
      loss_trades: 0,
      win_rate: 0,
      open_position: null,
      trades: [],
    }
  }

  const effectiveLotSize = Math.max(1, Math.round(lotSize || 1))
  const lastCandleTime = candles[candles.length - 1].time

  // Filter markers up to the current revealed replay candle
  const activeMarkers = markers
    .filter((m) => m.time <= lastCandleTime)
    .sort((a, b) => a.time - b.time)

  let balance = initialCapital
  const closedTrades: ReplayTrade[] = []
  let activePos: {
    id: string
    side: 'BUY' | 'SELL'
    qty: number
    entry_price: number
    entry_time: number
    sl: number | null
    tp: number | null
    markerIdx: number
  } | null = null

  // Map candle times to indices for quick sequential lookup
  const candleMap = new Map<number, number>()
  for (let i = 0; i < candles.length; i++) {
    candleMap.set(candles[i].time, i)
  }

  let nextMarkerIdx = 0

  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]

    // 1. Check if an active position hits SL or TP on candle i
    if (activePos != null) {
      let exitPrice: number | null = null
      let exitReason: ReplayTrade['exit_reason'] | null = null

      if (activePos.side === 'BUY') {
        if (activePos.sl != null && c.low <= activePos.sl) {
          exitPrice = activePos.sl
          exitReason = 'Stop Loss (SL)'
        } else if (activePos.tp != null && c.high >= activePos.tp) {
          exitPrice = activePos.tp
          exitReason = 'Take Profit (TP)'
        }
      } else {
        // SELL / SHORT
        if (activePos.sl != null && c.high >= activePos.sl) {
          exitPrice = activePos.sl
          exitReason = 'Stop Loss (SL)'
        } else if (activePos.tp != null && c.low <= activePos.tp) {
          exitPrice = activePos.tp
          exitReason = 'Take Profit (TP)'
        }
      }

      if (exitPrice != null && exitReason != null) {
        const pnl =
          activePos.side === 'BUY'
            ? (exitPrice - activePos.entry_price) * activePos.qty
            : (activePos.entry_price - exitPrice) * activePos.qty
        const return_pct =
          activePos.entry_price > 0
            ? ((exitPrice - activePos.entry_price) / activePos.entry_price) *
              100 *
              (activePos.side === 'BUY' ? 1 : -1)
            : 0

        balance += pnl
        closedTrades.push({
          id: activePos.id,
          symbol,
          side: activePos.side,
          qty: activePos.qty,
          entry_price: activePos.entry_price,
          entry_time: activePos.entry_time,
          exit_price: exitPrice,
          exit_time: c.time,
          pnl,
          return_pct,
          exit_reason: exitReason,
        })
        activePos = null
      }
    }

    // 2. Check if a new signal triggers on candle i
    while (nextMarkerIdx < activeMarkers.length && activeMarkers[nextMarkerIdx].time <= c.time) {
      const marker = activeMarkers[nextMarkerIdx]
      nextMarkerIdx++

      if (marker.time === c.time) {
        // If an existing position of opposite side is open, close it (Reversal)
        if (activePos != null && activePos.side !== marker.side) {
          const exitPrice = marker.price || c.close
          const pnl =
            activePos.side === 'BUY'
              ? (exitPrice - activePos.entry_price) * activePos.qty
              : (activePos.entry_price - exitPrice) * activePos.qty
          const return_pct =
            activePos.entry_price > 0
              ? ((exitPrice - activePos.entry_price) / activePos.entry_price) *
                100 *
                (activePos.side === 'BUY' ? 1 : -1)
              : 0

          balance += pnl
          closedTrades.push({
            id: activePos.id,
            symbol,
            side: activePos.side,
            qty: activePos.qty,
            entry_price: activePos.entry_price,
            entry_time: activePos.entry_time,
            exit_price: exitPrice,
            exit_time: c.time,
            pnl,
            return_pct,
            exit_reason: 'Reversal (Signal Flip)',
          })
          activePos = null
        }

        // Open new position on this signal if no position in same direction is already held
        if (activePos == null) {
          const entryPrice = marker.price || c.close
          let sl: number | null = null
          let tp: number | null = null

          if (marker.side === 'BUY') {
            const risk = marker.ht != null && marker.ht < entryPrice ? entryPrice - marker.ht : entryPrice * 0.015
            sl = marker.ht != null && marker.ht < entryPrice ? marker.ht : entryPrice - risk
            tp = entryPrice + risk * 2 // 1:2 Risk-Reward ratio
          } else {
            const risk = marker.ht != null && marker.ht > entryPrice ? marker.ht - entryPrice : entryPrice * 0.015
            sl = marker.ht != null && marker.ht > entryPrice ? marker.ht : entryPrice + risk
            tp = entryPrice - risk * 2 // 1:2 Risk-Reward ratio
          }

          activePos = {
            id: `replay-${marker.time}-${marker.side}`,
            side: marker.side,
            qty: effectiveLotSize,
            entry_price: entryPrice,
            entry_time: c.time,
            sl,
            tp,
            markerIdx: nextMarkerIdx - 1,
          }
        }
      }
    }
  }

  // 3. Compute Open Position floating unrealized P&L
  let openPosition: ReplayPosition | null = null
  let unrealizedPnL = 0

  if (activePos != null) {
    const currentPrice = candles[candles.length - 1].close
    unrealizedPnL =
      activePos.side === 'BUY'
        ? (currentPrice - activePos.entry_price) * activePos.qty
        : (activePos.entry_price - currentPrice) * activePos.qty
    const return_pct =
      activePos.entry_price > 0
        ? ((currentPrice - activePos.entry_price) / activePos.entry_price) *
          100 *
          (activePos.side === 'BUY' ? 1 : -1)
        : 0

    openPosition = {
      id: activePos.id,
      symbol,
      side: activePos.side,
      qty: activePos.qty,
      entry_price: activePos.entry_price,
      entry_time: activePos.entry_time,
      sl: activePos.sl,
      tp: activePos.tp,
      current_price: currentPrice,
      unrealized_pnl: unrealizedPnL,
      return_pct,
    }
  }

  const realizedPnL = balance - initialCapital
  const totalPnL = realizedPnL + unrealizedPnL
  const equity = balance + unrealizedPnL
  const returnPct = (totalPnL / initialCapital) * 100

  const winTrades = closedTrades.filter((t) => t.pnl > 0).length
  const lossTrades = closedTrades.filter((t) => t.pnl < 0).length
  const winRate = closedTrades.length > 0 ? (winTrades / closedTrades.length) * 100 : 0

  return {
    initial_capital: initialCapital,
    balance,
    equity,
    realized_pnl: realizedPnL,
    unrealized_pnl: unrealizedPnL,
    total_pnl: totalPnL,
    return_pct: returnPct,
    n_trades: closedTrades.length,
    win_trades: winTrades,
    loss_trades: lossTrades,
    win_rate: winRate,
    open_position: openPosition,
    trades: closedTrades.slice().reverse(), // Newest first
  }
}
