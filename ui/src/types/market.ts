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
  | { type: 'live_status'; symbol: string; exchange: string; interval: Interval; status: string; source: string }
  | { type: 'candle'; symbol: string; exchange: string; interval: Interval; candle: Candle; ts: string }
  | { type: 'pong' }
  | { type: 'error'; detail: string }

export type FetchStatus = 'loading' | 'ready' | 'empty' | 'error'
export type Mode = 'live' | 'replay'
