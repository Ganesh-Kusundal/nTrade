/**
 * Morning VAH/VAL scalper — pure TS mirror of `MorningVAHVAL`
 * (`ntrade/engines/morning_vah_val.py`) operating over a candle array (the
 * revealed bars), so the UI draws the strategy's own entries/exits, frozen
 * morning VAH/VAL levels, and prior-day bias without a server round-trip.
 *
 * The setup (Mukul Chowdhury "first 15 minutes" method):
 *   - 2-minute bars are aggregated from the 1m close stream on an IST-aligned
 *     fixed bucket grid — identical bucketing to the Python engine.
 *   - The volume profile over 09:15–09:30 IST is built once at 09:30 and
 *     FROZEN (static VAH/VAL for the morning).
 *   - Prior-day close-vs-open drift decides the only tradeable direction
 *     (UP → longs only, DOWN → shorts only, SIDEWAYS → no trades).
 *   - Long: prior-day UP + a fake break below VAL + a bullish reversal closes
 *     back above VAL. Short: prior-day DOWN + a VAH test/rejection closes
 *     back below VAH.
 *   - T1 = the opposite VA level: book half, move the stop to breakeven,
 *     trail candle-by-candle. Hard close at 11:00 and at the session end.
 *
 * Strictly intraday: every IST day boundary resets the machine (fresh profile
 * window, new bias from the day that just completed) and force-closes any
 * open trade at the prior day's close.
 */

import type { Candle } from '../types/market'
import { istDateKey } from './istTime'
import { buildVolumeProfile, type VolumeProfile } from './indicators'

export type MorningVahValSide = 'BUY' | 'SELL'
export type MorningVahValReason = 'stop' | 'time_close' | 'session_close'

export interface MorningVahValTrade {
  side: MorningVahValSide
  /** Candle index (into the input) where the entry fires (bucket completion). */
  entryIndex: number
  /** Entry reference price (the 2m bar's close at signal time). */
  entry: number
  /** Initial stop at entry — static (the SL marker). */
  sl: number
  /** Current effective stop — equals `sl` until T1, then the trailing stop
   *  (the TSL marker). Updated as the BE-phase ratchet lifts/lowers it. */
  slNow: number
  /** True once T1 books and the runner trails (drives the TSL marker). */
  tslActive: boolean
  /** Every stop placement: the initial stop at entry + each ratchet step
   *  (breakeven at T1, then candle/chandelier trail). {bar index, price}. */
  stops: Array<{ index: number; price: number }>
  /** T1 = opposite VA level; null after the partial books. */
  tp: number | null
  sizing: 'full' | 'half'
  /** Candle index / price of the T1 partial book (50% at VAH/VAL). */
  partialIndex: number | null
  partial: number | null
  /** Final exit candle index / price / reason — null while the trade is open. */
  exitIndex: number | null
  exit: number | null
  reason: MorningVahValReason | null
}

/** Live strategy state for today — drives the signals panel's status line. */
export interface MorningVahValState {
  /** Today's tradeable bias (null until a full prior day completes). */
  bias: 'UP' | 'DOWN' | 'SIDEWAYS' | null
  /** Morning FRVP frozen (VAH/VAL/POC available to trade against). */
  profileReady: boolean
  /** The last processed bar sits inside the 09:30–11:00 entry window. */
  inEntryWindow: boolean
  /** Time has passed the 11:00 entry-window close on today's session. */
  windowClosed: boolean
}

/** Frozen morning FRVP level per IST day (VAH/VAL/POC — static after 09:30). */
export interface MorningVahValLevels {
  date: string
  vah: number
  val: number
  poc: number
}

/** Prior-day context per IST day (UP / DOWN / SIDEWAYS — none on day one). */
export interface MorningVahValBias {
  date: string
  bias: 'UP' | 'DOWN' | 'SIDEWAYS'
}

export interface MorningVahValResult {
  trades: MorningVahValTrade[]
  levels: MorningVahValLevels[]
  bias: MorningVahValBias[]
  /** Today's machine state (bias / profile / entry-window position). */
  state: MorningVahValState
}

export interface MorningVahValOptions {
  /** IST session window (default 09:15–15:30). */
  sessionStart?: string
  sessionEnd?: string
  /** FRVP freeze time (default 09:30 — profile over [sessionStart, profileEnd)). */
  profileEnd?: string
  /** Tradeable window (default 09:30–11:00 — hard close = entryEnd). */
  entryStart?: string
  entryEnd?: string
  /** EMA cluster periods for the 100% vs 50% sizing decision (default 10/20). */
  emaFast?: number
  emaSlow?: number
  /** Cluster tolerance as a fraction of the bar close (default 0.1, mirror of
   *  the backend's ema_cluster_tol_pct). */
  emaClusterTolPct?: number
  /** Prior-day drift (fraction) below this = SIDEWAYS (default 0.0015). */
  sidewayThreshold?: number
  slPad?: number
  /** Minimum bars in the FRVP window before the profile can freeze. */
  minProfileBars?: number
  /** Only take entries at the 10/20 EMA cluster (drop the 50% tier). */
  requireCluster?: boolean
  /** At T1 book half + breakeven (true) or ride full-size at breakeven (false). */
  bookPartial?: boolean
  /** BE-phase trail ratchet lookback in completed bars (1 = previous bar). */
  trailBack?: number
  /** Reversal close must clear VAL/VAH by this fraction (0 = any close). */
  reversalMarginPct?: number
  /**
   * 'candle' (per-bar ratchet) or 'chandelier' (day-extreme ± mult×ATR).
   * // ponytail: available-but-losing option (best ties candle at -0.27%,
   * // wider stops lose). Ceiling: futures STT floor + entry-filter trade
   * // count. Upgrade: re-sweep when options OHLCV (STT on premium) exists.
   */
  trailMode?: 'candle' | 'chandelier'
  /** Chandelier ATR multiplier (default 2.0, mirror of backend atr_mult). */
  atrMult?: number
  /** Chandelier ATR period (default 3, mirror of backend atr_period). */
  atrPeriod?: number
}

/**
 * Cost-drag-tuned preset — mirror of `MorningVAHVAL.TUNED` (the Python
 * strategy). Sweep-validated on 54 days of BANKNIFTY futures: cuts the net
 * loss from -12.9% to ~-0.3% (DD 13.2% -> 0.6%, win rate ~67%). The UI draws
 * the same configuration the paper trader runs.
 */
export const MORNING_VAH_VAL_TUNED = {
  requireCluster: true,
  reversalMarginPct: 0.001,
  bookPartial: true,
  trailBack: 1,
  sidewayThreshold: 0.0015,
  slPad: 30,
}

const toMinutes = (hhmm: string): number => {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

/** IST minutes-of-day for a wire UTC epoch (IST has no DST — fixed offset). */
function istMinuteOfDay(epochSeconds: number): number {
  const shifted = ((epochSeconds + 19800) % 86400 + 86400) % 86400
  return Math.floor(shifted / 60)
}

/** IST-aligned 2m bucket — identical to the Python engine's `_bucket`. */
function bucketOf(epochSeconds: number): number {
  return Math.floor((epochSeconds + 5.5 * 3600) / 120)
}

/** Minute-of-day of a 2m bucket's START (IST). */
function bucketStartMinute(bucket: number): number {
  return Math.floor(((bucket * 120) % 86400 + 86400) % 86400 / 60)
}

/** Standard exponential moving average over an array of closes. */
function ema(closes: number[], period: number): number[] {
  const k = 2 / (period + 1)
  const out: number[] = []
  let prev = 0
  for (let i = 0; i < closes.length; i++) {
    prev = i === 0 ? closes[i] : closes[i] * k + prev * (1 - k)
    out.push(prev)
  }
  return out
}

interface Bar2m {
  minute: number // IST minute at the bucket start
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/**
 * Run the morning VAH/VAL state machine over closed bars (the strategy reacts
 * to `on_candle_closed` — feed it the *completed* revealed bars). Returns the
 * trades (chronological; last is open if any), the frozen per-day levels, and
 * the per-day bias.
 */
export function runMorningVahVal(candles: Candle[], opts: MorningVahValOptions = {}): MorningVahValResult {
  const sessionStart = toMinutes(opts.sessionStart ?? '09:15')
  const sessionEnd = toMinutes(opts.sessionEnd ?? '15:30')
  const profileEnd = toMinutes(opts.profileEnd ?? '09:30')
  const entryStart = toMinutes(opts.entryStart ?? '09:30')
  const entryEnd = toMinutes(opts.entryEnd ?? '11:00')
  const hardClose = entryEnd
  const emaFast = opts.emaFast ?? 10
  const emaSlow = opts.emaSlow ?? 20
  const emaClusterTolPct = opts.emaClusterTolPct ?? 0.1
  const sidewayThreshold = opts.sidewayThreshold ?? 0.0015
  const slPad = opts.slPad ?? 0
  const minProfileBars = opts.minProfileBars ?? 2
  const requireCluster = opts.requireCluster ?? false
  const bookPartial = opts.bookPartial ?? true
  const trailBack = opts.trailBack ?? 1
  const reversalMarginPct = opts.reversalMarginPct ?? 0
  const trailMode = opts.trailMode ?? 'candle'
  const atrMult = opts.atrMult ?? 2
  const atrPeriod = opts.atrPeriod ?? 3

  const trades: MorningVahValTrade[] = []
  const levels: MorningVahValLevels[] = []
  const biasOut: MorningVahValBias[] = []

  let dayKey = ''
  let dayRows: Candle[] = []
  let prevOpen = 0
  let prevClose = 0
  let hasPrevDay = false
  let bias: 'UP' | 'DOWN' | 'SIDEWAYS' | null = null

  let bars: Bar2m[] = []
  let pending: (Bar2m & { bucket: number }) | null = null
  let profile: VolumeProfile | null = null
  let vah = 0
  let val = 0

  // Active trade — tracked as (index into trades) + mutable SL.
  let activeIdx = -1
  let phase: 'entry' | 'be' = 'entry'
  let activeSl = 0
  let activeEntry = 0
  let activeTp: number | null = null
  let activeDayHigh = 0
  let activeDayLow = 0

  // ATR over completed 2m bars — mirrors the backend's `atr` exactly (true
  // range with pandas' max-of-NaN skip → TR row 0 is high-low, then
  // ewm(alpha=1/n, adjust=True, min_periods=n) at the last row) so chandelier
  // stops land on the same exit as the Python engine.
  const atrNow = (): number => {
    const n = bars.length
    if (n < atrPeriod) return 0
    const trs: number[] = []
    for (let i = 0; i < n; i++) {
      const h = bars[i].high, l = bars[i].low
      if (i === 0) trs.push(h - l) // no prev close → NaN terms skipped by max
      else {
        const pc = bars[i - 1].close
        trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)))
      }
    }
    const alpha = 1 / atrPeriod
    let num = 0, den = 0, w = 1
    for (let i = trs.length - 1; i >= 0; i--) {
      num += w * trs[i]
      den += w
      w *= 1 - alpha
    }
    const v = num / den
    return v > 0 ? v : 0
  }

  const emaCluster = (close: number): boolean => {
    if (bars.length < emaSlow) return false
    const closes = bars.map((b) => b.close)
    const fast = ema(closes, emaFast)[closes.length - 1] // tail value (mirror of .iloc[-1])
    const slow = ema(closes, emaSlow)[closes.length - 1]
    if (!(fast > 0 && slow > 0)) return false
    const tol = emaClusterTolPct * close
    return Math.abs(close - fast) <= tol && Math.abs(close - slow) <= tol
  }

  const enter = (side: MorningVahValSide, cur: Bar2m, prior: Bar2m, atIndex: number): void => {
    const entry = cur.close
    let sl: number
    let tp: number | null
    let valid: boolean
    if (side === 'BUY') {
      sl = Math.min(cur.low, prior.low) - slPad
      tp = vah
      valid = sl < entry && entry < tp
    } else {
      sl = Math.max(cur.high, prior.high) + slPad
      tp = val
      valid = tp !== 0 && tp < entry && entry < sl
    }
    if (!valid || !(sl > 0 && (tp ?? 0) > 0)) return
    const full = emaCluster(entry)
    if (requireCluster && !full) return // only EMA-cluster entries when required
    trades.push({
      side, entryIndex: atIndex, entry, sl,
      slNow: sl, tslActive: false, stops: [{ index: atIndex, price: sl }],
      tp, sizing: full ? 'full' : 'half',
      partialIndex: null, partial: null, exitIndex: null, exit: null, reason: null,
    })
    activeIdx = trades.length - 1
    phase = 'entry'
    activeSl = sl
    activeEntry = entry
    activeTp = tp
    activeDayHigh = cur.high
    activeDayLow = cur.low
  }

  const closeTrade = (atIndex: number, price: number, reason: MorningVahValReason): void => {
    const t = trades[activeIdx]
    if (!t) return
    t.exitIndex = atIndex
    t.exit = price
    t.reason = reason
    activeIdx = -1
  }

  // T1 handling: book half (classic) or breakeven + full-size ride. Mirrors
  // the backend's `_t1` (book_partial / qty_left bookkeeping are irrelevant to
  // the drawing mirror — fills happen server-side).
  const t1 = (atIndex: number, price: number): void => {
    const t = trades[activeIdx]
    if (!t) return
    if (bookPartial) {
      t.partialIndex = atIndex
      t.partial = price
    }
    t.tp = null
    phase = 'be'
    t.tslActive = true
    activeSl = activeEntry   // cost-to-cost on the runner
    t.slNow = activeSl
    t.stops.push({ index: atIndex, price: activeSl })
    activeTp = null
  }

  // Manage exits + entries for one completed 2m bar.
  const runOnBar = (bar: Bar2m, atIndex: number): void => {
    // Hard time gates first — absolute, win over everything.
    if (activeIdx >= 0) {
      if (bar.minute >= hardClose) {
        closeTrade(atIndex, bar.close, 'time_close')
        return
      }
      if (bar.minute >= sessionEnd) {
        closeTrade(atIndex, bar.close, 'session_close')
        return
      }
      const t = trades[activeIdx]
      if (t.side === 'BUY') {
        if (bar.low <= activeSl) {
          closeTrade(atIndex, activeSl, 'stop')
          return
        }
        if (phase === 'entry' && activeTp != null && bar.high >= activeTp) {
          t1(atIndex, activeTp)
        } else if (phase === 'be') {
          activeDayHigh = Math.max(activeDayHigh, bar.high)
          activeDayLow = Math.min(activeDayLow, bar.low)
          let next = activeSl
          if (trailMode === 'chandelier') {
            const a = atrNow()
            if (a > 0) {
              const trail = activeDayHigh - atrMult * a
              if (trail > next) next = trail
            }
          } else {
            const prior = bars[bars.length - 1 - trailBack]
            if (prior && prior.low > next) next = prior.low // ratchet toward day high
          }
          if (next !== activeSl) {
            activeSl = next
            t.slNow = next
            t.stops.push({ index: atIndex, price: next })
          }
        }
      } else {
        if (bar.high >= activeSl) {
          closeTrade(atIndex, activeSl, 'stop')
          return
        }
        if (phase === 'entry' && activeTp != null && bar.low <= activeTp) {
          t1(atIndex, activeTp)
        } else if (phase === 'be') {
          activeDayHigh = Math.max(activeDayHigh, bar.high)
          activeDayLow = Math.min(activeDayLow, bar.low)
          let next = activeSl
          if (trailMode === 'chandelier') {
            const a = atrNow()
            if (a > 0) {
              const trail = activeDayLow + atrMult * a
              if (trail < next) next = trail
            }
          } else {
            const prior = bars[bars.length - 1 - trailBack]
            if (prior && prior.high < next) next = prior.high // ratchet toward day low
          }
          if (next !== activeSl) {
            activeSl = next
            t.slNow = next
            t.stops.push({ index: atIndex, price: next })
          }
        }
      }
      return
    }

    // Entries — only inside the tradeable window, with a frozen profile and a
    // prior-day bias, on the two completed 2m bars (cur = last, prior = prev).
    if (bars.length < 2 || !profile) return
    if (bar.minute < entryStart || bar.minute >= entryEnd) return
    if (bias !== 'UP' && bias !== 'DOWN') return
    const cur = bars[bars.length - 1]
    const prior = bars[bars.length - 2]
    if (bias === 'UP' && prior.low <= val) {
      if (cur.close > val * (1 + reversalMarginPct) && cur.close > cur.open) {
        enter('BUY', cur, prior, atIndex)
      }
    } else if (bias === 'DOWN' && prior.high >= vah) {
      if (cur.close < vah * (1 - reversalMarginPct) && cur.close < cur.open) {
        enter('SELL', cur, prior, atIndex)
      }
    }
  }

  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    const key = istDateKey(c.time)
    const minute = istMinuteOfDay(c.time)

    // Day boundary: bias from the day that just completed; force-close any
    // open trade at that day's close; fresh profile/bars/rows for today.
    if (key !== dayKey) {
      if (dayKey !== '') {
        if (hasPrevDay && prevOpen > 0) {
          const drift = prevClose / prevOpen - 1
          bias = drift > sidewayThreshold
            ? 'UP' as const
            : drift < -sidewayThreshold ? 'DOWN' as const : 'SIDEWAYS' as const
          biasOut.push({ date: dayKey, bias })
        }
        if (activeIdx >= 0) {
          // Mirrors the Python rollover: exit at the first candle of the new
          // day, priced at its close (the strategy force-closes overnight).
          closeTrade(i, c.close, 'session_close')
        }
      }
      dayKey = key
      hasPrevDay = true
      prevOpen = prevClose = 0
      dayRows = []
      bars = []
      pending = null
      profile = null
      vah = val = 0
    }

    if (hasPrevDay && prevOpen === 0) prevOpen = c.open
    prevClose = c.close
    dayRows.push(c)

    // Freeze the morning FRVP once the [sessionStart, profileEnd) window closes.
    if (!profile && minute >= profileEnd) {
      const windowBars = dayRows.filter((r) => {
        const m = istMinuteOfDay(r.time)
        return m >= sessionStart && m < profileEnd
      })
      if (windowBars.length >= minProfileBars) {
        const prof = buildVolumeProfile(windowBars)
        if (prof.vah > prof.val && prof.vah > 0) {
          profile = prof
          vah = prof.vah
          val = prof.val
          levels.push({ date: key, vah: prof.vah, val: prof.val, poc: prof.poc })
        }
      }
    }

    // 1m → 2m aggregation; the state machine runs on completed 2m bars. The
    // bucket lives on the pending bar (mirror of the Python `_pending["bucket"]`)
    // so a bucket change finalizes it before the new bar starts.
    const bucket = bucketOf(c.time)
    if (pending && pending.bucket !== bucket) {
      bars.push(pending)
      runOnBar(pending, i)
      pending = null
    }
    if (pending === null) {
      pending = {
        bucket,
        minute: bucketStartMinute(bucket),
        open: c.open, high: c.high, low: c.low, close: c.close,
        volume: c.volume || 0,
      }
    } else {
      const b = pending
      b.high = Math.max(b.high, c.high)
      b.low = Math.min(b.low, c.low)
      b.close = c.close
      b.volume += c.volume || 0
    }
  }

  const last = candles[candles.length - 1]
  const lastMinute = last ? istMinuteOfDay(last.time) : -1
  const state: MorningVahValState = {
    bias,
    profileReady: profile !== null,
    inEntryWindow: lastMinute >= entryStart && lastMinute < entryEnd,
    windowClosed: lastMinute >= entryEnd,
  }

  return { trades, levels, bias: biasOut, state }
}
