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
 */

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
}

// ---------------------------------------------------------------------------------------
// Convenience — toggle + param defaults for a registered strategy.
// ---------------------------------------------------------------------------------------

/** Toggle keys a strategy wants on by default (the page turns these on in
*  ``usePersistedState`` initial state). */
export function strategyIndicatorKeys(spec: StrategySpec): string[] {
  return spec.indicators
}
