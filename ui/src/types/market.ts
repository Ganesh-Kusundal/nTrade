/** Wire types mirroring the api/marketdata.py contracts. */

/** Normalized candle DTO — `time` is UTC epoch seconds. */
export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// 'Range' is a client-side chart type: the UI fetches 1m candles and builds
// price-based range bars locally (never sent to the wire interval).
export type Interval = '1m' | '5m' | '15m' | '1h' | '1D' | 'Range'

export const INTERVALS: Interval[] = ['1m', '5m', '15m', '1h', '1D', 'Range']

/** The wire intervals the backend accepts (Range is derived client-side). */
export type WireInterval = Exclude<Interval, 'Range'>

export interface Contract {
  contract_id: string
  symbol: string
  root: string
  exchange: string
  expiry: string // ISO date
  days_to_expiry: number
  is_expired: boolean
  is_front_month: boolean
  lot_size: number
  tick_size: number
  security_id: number | null
}

export interface Root {
  root: string
  exchange: string
  display_name: string
  n_contracts: number
  front_month: Contract | null
}

export interface ProviderInfo {
  provider: string
  instrument_master_loaded: boolean
  instrument_master_source: string | null
  live: boolean
  roots: Root[]
}

export interface Quote {
  symbol: string
  exchange: string
  ltp: number
  change: number
  change_pct: number
  high: number
  low: number
  open: number
  prev_close: number
  volume: number
  oi: number
  source: string
}

export interface CandlesResponse {
  symbol: string
  exchange: string
  interval: Interval
  source: string
  count: number
  candles: Candle[]
}

export interface ContractsResponse {
  root: string
  exchange: string
  contracts: Contract[]
  source: string
}

/**
 * Synthesized 1-second ticks for one bar, compact form: tick i sits at
 * ``time + i`` seconds with price ``prices[i]`` and quantity
 * ``quantities[i]``. Kept as arrays end-to-end (wire + memory) so a full
 * contract history (~19k bars x 60 ticks) stays a few MB instead of ~1M objects.
 */
export interface TickBar {
  time: number // bar start, UTC epoch seconds
  prices: number[]
  quantities: number[]
}

export interface TicksResponse {
  symbol: string
  exchange: string
  interval: Interval
  source: string
  seconds: number // ticks per bar (60 for 1m, 300 for 5m, ...)
  count: number
  bars: TickBar[]
}

export interface RootsResponse {
  roots: Root[]
  source: string
}

/** Live WS messages */
export type WsMessage =
  | { type: 'live_status'; symbol: string; exchange: string; interval: Interval; status: 'streaming' | 'off' | 'stale'; source: string; reason?: string }
  | { type: 'candle'; symbol: string; exchange: string; interval: Interval; candle: Candle; ts: string }
  | { type: 'overlays'; symbol: string; exchange: string; interval: Interval; overlays: ChartOverlays; strategy: StrategyPayload | null; ts?: number }
  | { type: 'pong' }
  | { type: 'error'; detail: string }

export type FetchStatus = 'loading' | 'ready' | 'empty' | 'error'
export type Mode = 'live' | 'replay'

// ---------------------------------------------------------------------------
// Server-owned chart overlays (single source of truth — computed by the
// backend OverlayPipeline from the SAME analytics/strategy code the paper and
// live engines run). The FE never reimplements this math. Shapes mirror
// api/analytics/overlay_pipeline.py OverlayDTO.to_dict().
// ---------------------------------------------------------------------------

export interface VwapPoint {
  time: number
  value: number | null
}

export interface VolumeProfileLevel {
  price: number
  volume: number
}

export interface VolumeProfilePayload {
  step: number
  poc: number
  vah: number
  val: number
  levels: VolumeProfileLevel[]
}

export interface AdxPoint {
  time: number
  adx: number | null
  plus_di?: number | null
  minus_di?: number | null
}

export interface AdxPayload {
  period: number
  series: AdxPoint[]
}

export interface ChartOverlays {
  vwap: VwapPoint[] | null
  vwap_upper: VwapPoint[] | null
  vwap_lower: VwapPoint[] | null
  volume_profile: VolumeProfilePayload | null
  adx?: AdxPayload | null
}

/** A strategy signal produced by the backend replay (entry or exit marker). */
export interface StrategySignal {
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  phase?: string | null
  exit_reason?: string | null
  intent_price?: number | null
  sl?: number | null
  tp?: number | null
  reference_price?: number | null
}

export interface StrategyLevel {
  date: string
  vah: number
  val: number
  poc: number
}

export interface HalfTrendMarker {
  index: number
  time: number
  side: 'BUY' | 'SELL'
  price: number
  ht: number | null
  trend: number
}

export interface HalfTrendSeries {
  ht: (number | null)[]
  trend: number[]
  atrHigh: (number | null)[]
  atrLow: (number | null)[]
}

export interface StrategyPayload {
  id: string
  kind?: string
  markers?: HalfTrendMarker[]
  series?: HalfTrendSeries
  signals?: StrategySignal[]
  levels?: StrategyLevel[]
  phase?: string | null
  bias?: string | null
}

export interface ChartResponse {
  symbol: string
  exchange: string
  interval: string
  source: string
  count: number
  candles: Candle[]
  overlays: ChartOverlays
  strategy: StrategyPayload | null
  range_bars?: unknown[] | null
  reason?: string | null  // why a broker returned no data (vs. no history)
}
