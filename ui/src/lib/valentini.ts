/**
 * Valentini (Fabio) scalper — pure TS mirror of `ValentiniScalper`
 * (`ntrade/engines/strategies.py`) operating over a candle array (the
 * revealed replay bars), so the UI can draw the strategy's own entries and
 * exits without a server round-trip.
 *
 * Triple-A state machine over closed bars:
 *   waiting → absorbing (absorption bar seen) → accumulating (price
 *   consolidates back near the absorption level for 2+ bars) → signal
 *   (aggression: close beyond VWAP in the absorption's direction, not
 *   extended past the ±2σ band) → entry with SL/TP → managed exit
 *   (stop / target / 0.5R breakeven trail / hard session close).
 *
 * STRICTLY INTRADAY: the machine is reset at every IST day boundary — an
 * open trade is force-closed at the prior day's close, the phase is wiped,
 * and absorptions are detected per trading session — so no position or
 * setup ever carries overnight and replay results are day-accurate.
 *
 * The same caveat as the backend applies: no trade tape on Dhan, so this is
 * the OHLCV-derived approximation (absorption + VWAP + profile), not true
 * order flow. Range size: explicit `rangeSize` wins, else auto ATR(14) on
 * the contract's tick grid.
 *
 * Both analytics (absorptions, VWAP/bands) are precomputed once per call in
 * single passes — the state machine then runs O(n) over them, so replaying a
 * 3-day window stays cheap even when a bar completes every tick.
 */

import type { Candle } from '../types/market'
import { istDateKey } from './istTime'
import { calcAutoRange, atrSeries, buildRangeBars, swingBias, type RangeBar } from './rangeBars'
import { buildVolumeProfile, detectAbsorptions, type Absorption } from './indicators'

export type ValentiniPhase = 'waiting' | 'absorbing' | 'accumulating' | 'signal'

export type ExitReason = 'stop' | 'target' | 'session_close'

export interface ValentiniTrade {
  side: 'BUY' | 'SELL'
  /** Bar index (into the input candles) where the entry fires. */
  entryIndex: number
  entry: number
  sl: number
  tp: number
  rr: number
  /** Exit bar index / price / reason — null while the trade is open. */
  exitIndex: number | null
  exit: number | null
  reason: ExitReason | null
}

export interface ValentiniResult {
  phase: ValentiniPhase
  /** All trades in chronological order (last is the open one, if any). */
  trades: ValentiniTrade[]
  /** Absorption that armed the current phase (null before one is seen). */
  lastAbsorption: Absorption | null
}

export interface ValentiniOptions {
  /** Explicit range size (price). Auto ATR(14) on the tick grid when omitted. */
  rangeSize?: number
  tickSize?: number
  atrPeriod?: number
  warmup?: number
  absVolumeMult?: number
  absRangeThreshold?: number
  absLookback?: number
  tpMultiplier?: number
  minRr?: number
  /** IST session window (default 09:15–15:25, matching the strategy). */
  sessionStart?: string // "HH:MM" IST
  sessionEnd?: string // "HH:MM" IST
  fadeExtended?: boolean
  /** Require the trailing OHLCV CVD proxy to agree with the VWAP direction. */
  requireCvd?: boolean
  /** Bars of CVD used for the aggression confirmation (default 3). */
  cvdConfirm?: number
  /** Volume vote multiplier for the direction gate (default 1.0). */
  directionVolumeMult?: number
  /** Impulse span multiple that starts a new leg (default 2.0). */
  legImpulseMult?: number
  /** Recent-2-bar volume vs prior-median threshold for accumulation (1.5). */
  accumVolumeMult?: number
  /** R-multiple of profit that arms the swing-pivot trail (default 1.0). */
  trailArmMult?: number
  /** New-swing-extreme volume fraction of impulse volume = divergence (0.6). */
  divergenceVolumeMult?: number
  /** Leg-POC distance multiple marking an overextended reversal zone (2.0). */
  reverseExtensionMult?: number
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

function median(xs: number[]): number {
  if (xs.length === 0) return 0
  const s = [...xs].sort((a, b) => a - b)
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

/** Per-bar session VWAP + ±2σ volume-weighted bands (IST session = trading day). */
function sessionVwapBands(candles: Candle[]): Array<{ vwap: number; upper: number; lower: number } | null> {
  const out: Array<{ vwap: number; upper: number; lower: number } | null> = []
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
    if (vol <= 0) {
      out.push(null)
      continue
    }
    const typical = (c.high + c.low + c.close) / 3
    cumVol += vol
    cumPV += typical * vol
    const v = cumPV / cumVol
    cumVar += (typical - v) ** 2 * vol
    const sigma = Math.sqrt(cumVar / cumVol)
    out.push({ vwap: v, upper: v + 2 * sigma, lower: v - 2 * sigma })
  }
  return out
}

/**
 * Run the Valentini state machine over closed bars (the strategy reacts to
 * `on_candle_closed` — feed it the *completed* revealed bars, not the
 * in-progress one). Returns the phase, the trade list (chronological), and
 * the last absorption seen.
 */
export function runValentini(candles: Candle[], opts: ValentiniOptions = {}): ValentiniResult {
  const warmup = opts.warmup ?? 15
  const absLookback = opts.absLookback ?? 5
  const tpMultiplier = opts.tpMultiplier ?? 2.0
  const minRr = opts.minRr ?? 1.5
  const sessionStart = toMinutes(opts.sessionStart ?? '09:15')
  const sessionEnd = toMinutes(opts.sessionEnd ?? '15:25')
  const fadeExtended = opts.fadeExtended ?? true
  const requireCvd = opts.requireCvd ?? true
  const cvdConfirm = opts.cvdConfirm ?? 3
  const rangeSize = opts.rangeSize && opts.rangeSize > 0
    ? opts.rangeSize
    : calcAutoRange(candles, opts.atrPeriod ?? 14, 1, opts.tickSize)

  if (candles.length === 0) {
    return { phase: 'waiting', trades: [], lastAbsorption: null }
  }

  const step = Math.max(
    rangeSize || 1.0,
    (() => {
      const a = atrSeries(candles, opts.atrPeriod ?? 14)
      const last = a[a.length - 1]
      return Number.isFinite(last) ? last : 0
    })(),
  )
  const vwap = sessionVwapBands(candles)
  // Per-session absorption detection: each IST trading day is sliced and
  // detected separately, so yesterday's volume/range context can never bleed
  // into today's early bars (strict intraday — no cross-day signal carry).
  const absorptions: Absorption[] = []
  {
    let s0 = 0
    let sKey = istDateKey(candles[0].time)
    const detectOpts = {
      avgVolumeMult: opts.absVolumeMult ?? 1.5,
      rangeThreshold: opts.absRangeThreshold ?? 0.5,
      rangeSize,
    }
    for (let i = 1; i <= candles.length; i++) {
      const key = i < candles.length ? istDateKey(candles[i].time) : null
      if (key !== sKey) {
        const offset = s0
        for (const a of detectAbsorptions(candles.slice(s0, i), detectOpts)) {
          absorptions.push({ ...a, barIndex: a.barIndex + offset })
        }
        s0 = i
        sKey = key ?? ''
      }
    }
  }

  const trades: ValentiniTrade[] = []
  let phase: ValentiniPhase = 'waiting'
  let lastAbsorption: Absorption | null = null
  let absorptionWindowIdx = 0
  let active: { side: 'BUY' | 'SELL'; entry: number; sl: number; tp: number; rr: number } | null = null

  const inSession = (i: number): boolean => {
    const m = istMinuteOfDay(candles[i].time)
    return m >= sessionStart && m <= sessionEnd
  }
  const close = (i: number): number => candles[i].close

  // ponytail: session-keyed volume profile (guide "location") + prior-day POC
  // used as the aggression target. Built from the bars of each IST day.
  let priorPoc: number | null = null
  let dayBars: Candle[] = []
  let legStartIdx = 0
  let sessionVwap = 0
  let rangeBars: RangeBar[] = []
  let profileReady = false
  let sessionPoc = 0
  let sessionVal = 0
  let sessionVah = 0
  const valueEdgeOk = (side: 'BUY' | 'SELL', price: number): boolean => {
    // Absorption must sit at the value edge (VAL for longs, VAH for shorts).
    if (!profileReady) return true // warm-up: no profile yet
    return side === 'BUY'
      ? Math.abs(price - sessionVal) <= 2 * step
      : Math.abs(price - sessionVah) <= 2 * step
  }
  const inBalance = (c: number): boolean => {
    if (!profileReady || sessionVah <= sessionVal) return false // no VA / degenerate
    if (!(c >= sessionVal && c <= sessionVah)) return false
    return Math.abs(c - sessionPoc) / (sessionVah - sessionVal) < 0.25
  }
  const cvdAgrees = (side: 'BUY' | 'SELL', idx: number): boolean => {
    if (!requireCvd) return true
    // Trailing OHLCV CVD proxy: signed volume (close vs open) over cvdConfirm bars.
    const start = Math.max(0, idx - cvdConfirm + 1)
    let sum = 0
    for (let j = start; j <= idx; j++) {
      const cc = candles[j]
      const d = cc.close - cc.open
      sum += d > 0 ? (cc.volume || 0) : d < 0 ? -(cc.volume || 0) : 0
    }
    return side === 'BUY' ? sum >= 0 : sum <= 0
  }
  const volumeSupports = (): boolean => {
    if (dayBars.length === 0) return false
    const leg = dayBars.slice(legStartIdx)
    if (leg.length === 0) return false
    const prior = dayBars.slice(0, legStartIdx).map((b) => b.volume || 0)
    const base = prior.length ? median(prior) : 0
    if (base <= 0) return true
    const legVol = leg.reduce((a, b) => a + (b.volume || 0), 0)
    return legVol >= (opts.directionVolumeMult ?? 1.0) * base
  }
  const direction = (closePrice: number): 'BUY' | 'SELL' | null => {
    if (!volumeSupports()) return null
    const vwapSide = closePrice > sessionVwap
      ? 'BUY' as const
      : closePrice < sessionVwap ? 'SELL' as const : null
    if (vwapSide === null) return null
    const struct = swingBias(rangeBars)
    if (struct === null) return vwapSide
    return struct === vwapSide ? vwapSide : null
  }

  let dayKey = ''
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    const key = istDateKey(c.time)

    // Strict intraday: a new IST day force-closes any open trade at the prior
    // day's close (even when the data jumps straight from 15:25 to the next
    // 09:15 with no out-of-session bar to trigger the gate) and wipes the
    // phase machine — no position or setup ever carries overnight.
    if (key !== dayKey) {
      if (active && trades.length > 0) {
        const prev = i > 0 ? candles[i - 1] : c
        trades[trades.length - 1] = {
          ...trades[trades.length - 1],
          exitIndex: i > 0 ? i - 1 : 0,
          exit: prev.close,
          reason: 'session_close',
        }
        active = null
      }
      // Stash the prior day's POC as the aggression target, then rebuild
      // today's profile from scratch (strict intraday — no cross-day carry).
      if (sessionVah > sessionVal) priorPoc = sessionPoc
      dayKey = key
      phase = 'waiting'
      lastAbsorption = null
      absorptionWindowIdx = 0
      dayBars = []
      legStartIdx = 0
      sessionVwap = 0
      rangeBars = []
    }
    // Rebuild today's location profile from the bars seen so far this session.
    dayBars.push(c)
    // Advance the leg anchor: the last 1m candle with span >= legImpulseMult
    // * step starts a fresh leg (mirror of strategies.py on_candle_closed).
    const impThr = (opts.legImpulseMult ?? 2.0) * step
    let legStart = legStartIdx
    for (let k = dayBars.length - 1; k >= 0; k--) {
      if (dayBars[k].high - dayBars[k].low >= impThr) { legStart = k; break }
    }
    legStartIdx = legStart
    const prof = buildVolumeProfile(dayBars.slice(legStartIdx), rangeSize || undefined)
    sessionPoc = prof.poc
    sessionVal = prof.val
    sessionVah = prof.vah
    sessionVwap = vwap[i]?.vwap ?? 0
    rangeBars = buildRangeBars(dayBars, rangeSize, { atrPeriod: opts.atrPeriod ?? 14, tickSize: opts.tickSize })
    profileReady = true

    // Trade management first — an open position exits on SL/TP/breakeven/
    // session close and blocks new entries.
    if (active) {
      const low = c.low
      const high = c.high
      if (active.side === 'BUY') {
        if (low <= active.sl) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.sl, reason: 'stop' }
          active = null
        } else if (high >= active.tp) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.tp, reason: 'target' }
          active = null
        } else {
          const half = active.entry + 0.5 * (active.tp - active.entry)
          if (high >= half && active.sl < active.entry) active.sl = active.entry // trail to breakeven
        }
      } else {
        if (high >= active.sl) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.sl, reason: 'stop' }
          active = null
        } else if (low <= active.tp) {
          trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: active.tp, reason: 'target' }
          active = null
        } else {
          const half = active.entry - 0.5 * (active.entry - active.tp)
          if (low <= half && active.sl > active.entry) active.sl = active.entry // trail to breakeven
        }
      }
      if (active && !inSession(i)) {
        trades[trades.length - 1] = { ...trades[trades.length - 1], exitIndex: i, exit: close(i), reason: 'session_close' }
        active = null
      }
      continue
    }

    // Warm-up + session gate: the strategy only arms setups inside the
    // trading session (never overnight risk).
    if (i < warmup || !inSession(i)) continue

    // Phase machine (mirrors `_update_phase`). Absorptions are precomputed,
    // but each evaluation must only see bars up to the current close (the
    // backend recomputes over the frame-so-far) — filter out any future
    // absorption before the recency check. The same-IST-day guard keeps a
    // prior day's absorption from arming today's setup.
    const recent = absorptions.filter(
      (a) =>
        a.barIndex <= i &&
        a.barIndex >= i + 1 - absLookback &&
        istDateKey(candles[a.barIndex].time) === key,
    )

    if (phase === 'waiting') {
      // Only arm an absorption at the value edge (VAL/VAH), not mid-range.
      const armed = recent.filter((a) => valueEdgeOk(a.side, a.price))
      if (armed.length > 0) {
        lastAbsorption = armed[armed.length - 1]
        absorptionWindowIdx = lastAbsorption.barIndex
        phase = 'absorbing'
      }
      continue
    }

    if (phase === 'absorbing' && lastAbsorption) {
      const elapsed = Math.max(i - absorptionWindowIdx, 0)
      // Accumulation = price near the session POC (guide §4.1), confirmed by
      // recent-2-bar volume vs the prior-median (guide §4.2).
      if (elapsed >= 2 && Math.abs(close(i) - sessionPoc) <= 2 * step) {
        const vols = dayBars.map((b) => b.volume || 0)
        const recentVol = vols.slice(-2).reduce((a, b) => a + b, 0)
        const prior = vols.slice(0, -2)
        const avgVol = prior.length ? median(prior) : 0
        const volOk = avgVol <= 0 || recentVol >= (opts.accumVolumeMult ?? 1.5) * avgVol
        if (volOk) phase = 'accumulating'
      } else if (elapsed > absLookback * 3) {
        phase = 'waiting' // setup died — price ran away, expire it
        continue
      }
    }

    if (phase === 'accumulating' && lastAbsorption) {
      const elapsed = Math.max(i - absorptionWindowIdx, 0)
      if (elapsed > absLookback * 3) {
        phase = 'waiting'
        continue
      }
      const side = lastAbsorption.side
      const v = vwap[i]
      if (!v) continue
      // Aggression: VWAP bias + CVD proxy agreement; skip mid-value balance.
      if (inBalance(close(i))) continue
      const dirn = direction(close(i))
      if (side === 'BUY' && dirn === 'BUY') {
        if (fadeExtended && close(i) > v.upper) continue // extended — wait for pullback
        if (!cvdAgrees('BUY', i)) continue
        phase = 'signal'
      } else if (side === 'SELL' && dirn === 'SELL') {
        if (fadeExtended && close(i) < v.lower) continue // extended — wait for pullback
        if (!cvdAgrees('SELL', i)) continue
        phase = 'signal'
      }
      if (phase === 'signal') {
        // Stop: VAL − step (long) / VAH + step (short). Target: prefer the
        // prior session's POC when its R:R clears minRr, else R-multiple.
        const entry = close(i)
        const sl = side === 'BUY'
          ? (sessionVah > sessionVal ? sessionVal - step : lastAbsorption.price - step)
          : (sessionVah > sessionVal ? sessionVah + step : lastAbsorption.price + step)
        let tp = side === 'BUY'
          ? entry + (entry - sl) * tpMultiplier
          : entry - (sl - entry) * tpMultiplier
        let rr = side === 'BUY'
          ? (entry > sl ? (tp - entry) / (entry - sl) : 0)
          : (sl > entry ? (entry - tp) / (sl - entry) : 0)
        if (priorPoc !== null) {
          const tpPoc = priorPoc
          const rrPoc = side === 'BUY'
            ? (entry > sl && tpPoc > entry ? (tpPoc - entry) / (entry - sl) : 0)
            : (sl > entry && tpPoc < entry ? (entry - tpPoc) / (sl - entry) : 0)
          if (rrPoc >= minRr) {
            tp = tpPoc
            rr = rrPoc
          }
        }
        if (rr >= minRr) {
          trades.push({ side, entryIndex: i, entry, sl, tp, rr, exitIndex: null, exit: null, reason: null })
          active = { side, entry, sl, tp, rr }
        }
        phase = 'waiting' // one signal per setup
      }
    }
  }

  return { phase, trades, lastAbsorption }
}
