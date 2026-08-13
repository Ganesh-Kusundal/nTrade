/**
 * Indicator + strategy registry — the client-side mirror of the backend
 * ``ntrade.registry`` (``indicator`` / ``strategy``).
 *
 * Single source of truth for what the UI can draw: each key has an id that
 * matches the Python registry exactly, a ``run`` fn (re-exported from the
 * existing pure-TS mirrors), default params, and whether it produces a full
 * series (so the chart can draw it) or a single latest value.
 *
 * Adding a new indicator or strategy = register here (both backend Python and
 * this TS file) — zero edits to TradeScreen / ChartPanel. The mirrors stay
 * pure (no API round-trip) and headers-free: every indicator module pulls its
 * own deps from ``../types/market`` and the pure-TS helpers.
 *
 * Parity with the backend is enforced by the integration test in
 * ``__tests__/registry.test.ts``: the two registries must carry the same keys
 * and a ``series`` flag consistent with how the chart actually renders them.
 */

import type { Candle } from '../types/market'
import { vwapSeries, buildVolumeProfile, detectAbsorptions, sessionProfileCandles } from './indicators'
import { runValentini, type ValentiniResult, type ValentiniTrade } from './valentini'
import { runMorningVahVal, type MorningVahValResult, type MorningVahValTrade } from './morningVahVal'

// ---------------------------------------------------------------------------------------
// Types — mirror of ``ntrade/registry.py`` IndicatorSpec / StrategySpec / PlotSpec
// ---------------------------------------------------------------------------------------

/** Plot shape for a ``series=true`` indicator (full candle-range output). */
export interface SeriesPlotSpec {
  kind: 'line' | 'bands' | 'markers' | 'histogram' | 'profile'
  key: string // stable render key (e.g. "vwap", "volume_profile", "absorptions")
  label: string
}

/** How to render an indicator when its toggle is on. ``series=false`` → scalar, no chart asset. */
export interface IndicatorSpec {
  id: string // exact match with ``ntrade.registry.indicator`` keys
  label: string
  category: 'volume' | 'price' | 'momentum' | 'order_flow' | 'profile'
  series: boolean
  plot?: SeriesPlotSpec
  run: (candles: Candle[], ...opts: any[]) => unknown
  /** Default params forwarded to the runner (mirrors Python init defaults). */
  defaultParams?: Record<string, unknown>
}

/** Strategy overlay spec — re-exported run + trades shape + default params. */
export interface StrategySpec {
  id: string // exact match with ``ntrade.registry.strategy`` keys
  label: string
  category: 'scalper' | 'momentum' | 'mean_reversion'
  run: (candles: Candle[], ...opts: any[]) => unknown
  defaultParams?: Record<string, unknown>
  /** Which indicator keys the strategy consumes (used by TradeScreen to seed
  *  toggle defaults — the strategy turns on its prerequisites). */
  indicators: string[]
}

// ---------------------------------------------------------------------------------------
// Indicator registry — re-exports every pure-TS indicator under a stable id.
// ---------------------------------------------------------------------------------------

export { sessionProfileCandles }
export const indicators: Record<string, IndicatorSpec> = {
  vwap: {
    id: 'vwap',
    label: 'VWAP (per session)',
    category: 'price',
    series: true,
    plot: { kind: 'bands', key: 'vwap', label: 'VWAP ± σ' },
    run: vwapSeries,
    defaultParams: { numStd: 2 },
  },
  volume_profile: {
    id: 'volume_profile',
    label: 'Volume Profile (FRVP)',
    category: 'profile',
    series: true,
    plot: { kind: 'profile', key: 'volume_profile', label: 'POC / VAH / VAL' },
    run: buildVolumeProfile,
  },
  absorptions: {
    id: 'absorption',
    label: 'Absorption Bars',
    category: 'order_flow',
    series: false,
    plot: { kind: 'markers', key: 'absorptions', label: 'Absorption ↑↓' },
    run: detectAbsorptions,
    defaultParams: { avgVolumeMult: 1.5, rangeThreshold: 0.5 },
  },
}

// ---------------------------------------------------------------------------------------
// Strategy registry — re-exports both scalpers under stable ids matching the
// backend ``ntrade.registry.strategy`` keys: ema_cross, valentini, morning_vah_val.
// ---------------------------------------------------------------------------------------

export interface ValentiniTradeShape extends ValentiniTrade {}
export interface MorningVahValTradeShape extends MorningVahValTrade {}

export const strategies: Record<string, StrategySpec> = {
  valentini: {
    id: 'valentini',
    label: 'Valentini Scalper (Fabio)',
    category: 'scalper',
    run: runValentini,
    defaultParams: {
      warmup: 15,
      absVolumeMult: 1.5,
      absRangeThreshold: 0.5,
      absLookback: 5,
      tpMultiplier: 2.0,
      minRr: 1.5,
      sessionStart: '09:15',
      sessionEnd: '15:25',
      fadeExtended: true,
      requireCvd: true,
      cvdConfirm: 3,
      directionVolumeMult: 1.0,
      legImpulseMult: 2.0,
      accumVolumeMult: 1.5,
      trailArmMult: 1.0,
      divergenceVolumeMult: 0.6,
      reverseExtensionMult: 2.0,
    },
    indicators: ['vwap', 'volume_profile', 'absorptions'],
  },
  morning_vah_val: {
    id: 'morning_vah_val',
    label: 'Morning VAH/VAL (Mukul)',
    category: 'scalper',
    run: runMorningVahVal,
    defaultParams: {
      sessionStart: '09:15',
      sessionEnd: '15:30',
      profileEnd: '09:30',
      entryStart: '09:30',
      entryEnd: '11:00',
      emaFastPeriod: 10,
      emaSlowPeriod: 20,
      emaMinDistPct: 0.5,
      frvpDistPct: 1.5,
    },
    indicators: ['vwap', 'volume_profile'],
  },
}

// Quick shape guards — used by TradeScreen to verify the runner returns the
// expected overlay shape without a full type-erasure downcast.
export function isValentiniResult(r: unknown): r is ValentiniResult {
  return typeof r === 'object' && r !== null && 'phase' in r && 'trades' in r && 'lastAbsorption' in r
}
export function isMorningVahValResult(r: unknown): r is MorningVahValResult {
  return typeof r === 'object' && r !== null && 'trades' in r && 'levels' in r && 'bias' in r
}

// ---------------------------------------------------------------------------------------
// Convenience — toggle + param defaults for a registered strategy.
// ---------------------------------------------------------------------------------------

/** Toggle keys a strategy wants on by default (the page turns these on in
*  ``usePersistedState`` initial state). */
export function strategyIndicatorKeys(spec: StrategySpec): string[] {
  return spec.indicators
}

/** Merge a strategy's default params with caller overrides and the session
*  window so TradeScreen can hand a complete opts object to ``spec.run``. */
export function strategyRun(opts: { candles: Candle[], spec: StrategySpec, overrides?: Record<string, unknown>, session?: { start?: string; end?: string } }): unknown {
  const base = (opts.spec.defaultParams ?? {}) as Record<string, unknown>
  const merged: Record<string, unknown> = { ...base }
  if (opts.session) {
    if (opts.session.start != null) merged['sessionStart'] = opts.session.start
    if (opts.session.end != null) merged['sessionEnd'] = opts.session.end
  }
  if (opts.overrides) Object.assign(merged, opts.overrides)
  return opts.spec.run(opts.candles, merged)
}
