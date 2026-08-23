/**
 * Indicator + strategy registry — the client-side *metadata mirror* of the
 * backend ``ntrade.registry`` (``indicator`` / ``strategy``).
 *
 * The FE is a renderer only: every indicator / strategy is computed by the
 * backend OverlayPipeline and served via ``/api/market/chart`` (+ WS overlay
 * patches). This file therefore holds NO math — only stable ids, labels,
 * default params, and plot metadata so the chart legend / strategy picker can
 * render. Adding a new indicator or strategy = register its metadata here AND
 * in the Python registry; zero edits to TradeScreen / ChartPanel.
 *
 * Parity with the backend is enforced by ``tests/test_catalog_endpoint``
 * (the two registries must carry the same keys).
 *
 * The static maps below are the **offline fallback**. At runtime the app calls
 * ``refreshCatalog()`` once (on bootstrap) to overlay the backend's
 * authoritative registry, so a newly registered strategy/indicator shows up in
 * the UI without a TS edit.
 */

import { fetchCatalog, type CatalogResponse } from '../api/client'

// ---------------------------------------------------------------------------------------
// Types — mirror of ``ntrade/registry.py`` IndicatorSpec / StrategySpec / PlotSpec
// ---------------------------------------------------------------------------------------

/** Plot shape for a ``series=true`` indicator (full candle-range output). */
export interface SeriesPlotSpec {
  kind: 'line' | 'bands' | 'markers' | 'histogram' | 'profile'
  key: string // stable render key (e.g. "vwap", "volume_profile", "absorptions")
  label: string
}

/** How to render an indicator when its toggle is on. */
export interface IndicatorSpec {
  id: string // exact match with ``ntrade.registry.indicator`` keys
  label: string
  category: 'volume' | 'price' | 'momentum' | 'order_flow' | 'profile'
  series: boolean
  plot?: SeriesPlotSpec
  /** Default params forwarded to the runner (mirrors Python init defaults). */
  defaultParams?: Record<string, unknown>
}

/** Strategy overlay metadata — id + label + draw state, no client run(). */
export interface StrategySpec {
  id: string // exact match with ``ntrade.registry.strategy`` keys
  label: string
  category: 'scalper' | 'momentum' | 'mean_reversion'
  defaultParams?: Record<string, unknown>
  /** Which indicator keys the strategy consumes (used by TradeScreen to seed
   *  toggle defaults — the strategy turns on its prerequisites). */
  indicators: string[]
}

// ---------------------------------------------------------------------------------------
// Indicator registry — display metadata only (the math lives on the backend).
// ---------------------------------------------------------------------------------------

export const indicators: Record<string, IndicatorSpec> = {
  vwap: {
    id: 'vwap',
    label: 'VWAP (per session)',
    category: 'price',
    series: true,
    plot: { kind: 'bands', key: 'vwap', label: 'VWAP ± σ' },
    defaultParams: { numStd: 2 },
  },
  volume_profile: {
    id: 'volume_profile',
    label: 'Volume Profile (FRVP)',
    category: 'profile',
    series: true,
    plot: { kind: 'profile', key: 'volume_profile', label: 'POC / VAH / VAL' },
  },
  adx: {
    id: 'adx',
    label: 'Average Directional Index (ADX 14)',
    category: 'momentum',
    series: true,
    plot: { kind: 'line', key: 'adx', label: 'ADX (14) +DI / -DI' },
    defaultParams: { adxPeriod: 14 },
  },
  absorptions: {
    id: 'absorption',
    label: 'Absorption Bars',
    category: 'order_flow',
    series: false,
    plot: { kind: 'markers', key: 'absorptions', label: 'Absorption ↑↓' },
    defaultParams: { avgVolumeMult: 1.5, rangeThreshold: 0.5 },
  },
}

// ---------------------------------------------------------------------------------------
// Strategy registry — display metadata only (the replay lives on the backend).
// ---------------------------------------------------------------------------------------

export const strategies: Record<string, StrategySpec> = {
  halftrend: {
    id: 'halftrend',
    label: 'HalfTrend',
    category: 'momentum',
    defaultParams: {
      amplitude: 2,
      channelDeviation: 2,
      atrPeriod: 100,
    },
    indicators: [],
  },
  // Static fallback entry so the UI can render ORB even before refreshCatalog()
  // runs. The backend catalog is the source of truth and overrides these.
  orb_vwap: {
    id: 'orb_vwap',
    label: 'Opening Range Breakout + VWAP',
    category: 'momentum',
    defaultParams: {
      riskPerTradePct: 1.0,
      emaFast: 9,
      emaSlow: 21,
      volumeMult: 1.5,
      targetRR: 2.0,
      maxTradesPerDay: 2,
    },
    indicators: ['vwap', 'ema'],
  },
}

// ---------------------------------------------------------------------------------------
// Catalog sync — overlay the backend registry onto the static mirrors.
// ---------------------------------------------------------------------------------------

/** Merge the backend catalog into the static mirrors (idempotent, run once). */
export function mergeCatalog(catalog: CatalogResponse): void {
  for (const s of catalog.strategies) {
    if (!strategies[s.id]) {
      strategies[s.id] = {
        id: s.id,
        label: s.label,
        category: 'momentum',
        defaultParams: {},
        indicators: s.indicators ?? [],
      }
    } else {
      // Keep static display fields but adopt backend truth for id/label/params.
      strategies[s.id].id = s.id
      strategies[s.id].label = s.label
      strategies[s.id].indicators = s.indicators ?? strategies[s.id].indicators
    }
  }
  for (const ind of catalog.indicators) {
    if (!indicators[ind.id]) {
      indicators[ind.id] = {
        id: ind.id,
        label: ind.label,
        category: 'price',
        series: ind.series,
        plot: ind.plot
          ? { kind: 'line', key: ind.plot.series_key ?? ind.id, label: ind.label }
          : undefined,
        defaultParams: ind.params ?? {},
      }
    }
  }
}

/** Fetch + merge the backend catalog (safe: never throws, keeps static fallback). */
export async function refreshCatalog(): Promise<void> {
  try {
    const catalog = await fetchCatalog()
    mergeCatalog(catalog)
  } catch {
    /* offline — static mirrors remain authoritative */
  }
}

// ---------------------------------------------------------------------------------------
// Convenience — toggle + param defaults for a registered strategy.
// ---------------------------------------------------------------------------------------

/** Toggle keys a strategy wants on by default (the page turns these on in
 *  ``usePersistedState`` initial state). */
export function strategyIndicatorKeys(spec: StrategySpec): string[] {
  return spec.indicators
}

export function strategyLabel(id: string): string {
  return strategies[id]?.label ?? id
}
