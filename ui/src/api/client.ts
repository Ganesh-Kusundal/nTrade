import type {
  CandlesResponse,
  ContractsResponse,
  Interval,
  ProviderInfo,
  Quote,
  RootsResponse,
  TicksResponse,
  WsMessage,
} from '../types/market'

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

/** Throw a readable error for non-2xx responses (backend sends JSON detail). */
async function request<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    }
  }
  const res = await fetch(url.toString())
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export interface PaperPosition {
  symbol: string
  quantity: number
  avg_price: number
  ltp: number
  pnl: number
}

export interface PaperTrade {
  order_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  ts: string
}

export interface PaperStatus {
  running: boolean
  symbol: string | null
  exchange: string | null
  lot_size: number
  initial_cash: number
  balance: number
  equity: number
  positions: PaperPosition[]
  trades: PaperTrade[]
  n_trades: number
  realized_pnl: number
  unrealized_pnl: number
  started_at: string | null
  error: string | null
}

export const api = {
  paperStart: (symbol: string, exchange: string = 'NFO', lotSize?: number) =>
    fetch(`${API_BASE}/paper/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, exchange, lot_size: lotSize }),
    }).then(async (res) => {
      if (!res.ok) {
        let detail = `${res.status} ${res.statusText}`
        try {
          const body = await res.json()
          if (typeof body?.detail === 'string') detail = body.detail
        } catch { /* non-JSON */ }
        throw new Error(detail)
      }
      return res.json() as Promise<PaperStatus>
    }),
  paperStop: () =>
    fetch(`${API_BASE}/paper/stop`, { method: 'POST' }).then((res) => res.json() as Promise<PaperStatus>),
  paperStatus: () => request<PaperStatus>('/paper/status'),
  provider: () => request<ProviderInfo>('/market/provider'),
  roots: () => request<RootsResponse>('/market/roots'),
  contracts: (root: string) => request<ContractsResponse>(`/market/roots/${encodeURIComponent(root)}/contracts`),
  candles: (
    symbol: string,
    interval: Interval,
    opts?: { start?: string; end?: string; limit?: number; exchange?: string },
  ) =>
    request<CandlesResponse>('/market/candles', {
      symbol,
      interval,
      exchange: opts?.exchange ?? 'NFO',
      start: opts?.start ?? '',
      end: opts?.end ?? '',
      limit: opts?.limit ?? '',
    }),
  ticks: (
    symbol: string,
    interval: Interval,
    exchange: string = 'NFO',
    opts?: { start?: string; end?: string },
  ) =>
    request<TicksResponse>('/market/ticks', {
      symbol,
      interval,
      exchange,
      start: opts?.start ?? '',
      end: opts?.end ?? '',
    }),
  quote: (symbol: string, exchange: string = 'NFO') =>
    request<Quote>('/market/quote', { symbol, exchange }),
}

export function wsUrl(): string {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/market`
}

/**
 * Reconnecting WebSocket for live candles.
 *
 * - Exponential backoff with jitter (1s → 30s cap) on reconnect.
 * - Resubscribes all active symbols after reconnect.
 * - One listener set; messages are dispatched by the store/hooks.
 */
export class MarketSocket {
  private ws: WebSocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private attempts = 0
  private closed = false
  private readonly subs = new Map<string, { symbol: string; exchange: string; interval: Interval }>()
  private readonly listeners = new Set<(msg: WsMessage) => void>()
  private readonly url: string
  /** 'off' = server live_status 'off'; 'stale' = no candles within timeout. */
  onStatus: (status: 'connected' | 'disconnected' | 'reconnecting' | 'off' | 'stale') => void = () => {}

  private serverStreaming = false
  private lastCandleAt = 0
  private readonly staleMs = 5000
  private watchdog: ReturnType<typeof setInterval> | null = null

  private armWatchdog(): void {
    if (this.watchdog !== null) return
    this.watchdog = setInterval(() => {
      if (!this.serverStreaming) return
      if (this.lastCandleAt > 0 && Date.now() - this.lastCandleAt > this.staleMs) {
        this.onStatus('stale')
      } else if (this.lastCandleAt > 0) {
        this.onStatus('connected')
      }
    }, 1000)
  }

  constructor(url: string = wsUrl()) {
    this.url = url
  }

  connect(): void {
    this.closed = false
    this.armWatchdog()
    this.open()
  }

  private open(): void {
    if (this.closed) return
    let ws: WebSocket
    try {
      ws = new WebSocket(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws = ws
    ws.onopen = () => {
      this.attempts = 0
      this.onStatus('connected')
      // Resubscribe everything with the proper wire shape after reconnect
      for (const sub of this.subs.values()) this.send({ type: 'subscribe', ...sub })
    }
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WsMessage
        // The server reports whether live streaming is actually on. When it is
        // off (market closed / no live feed), surface that as a distinct
        // status instead of pretending we're streaming.
        if (msg.type === 'live_status') {
          if (msg.status === 'off') {
            this.serverStreaming = false
            this.onStatus('off')
          } else if (msg.status === 'stale') {
            this.onStatus('stale')
          } else {
            this.serverStreaming = true
          }
        } else if (msg.type === 'candle') {
          this.lastCandleAt = Date.now()
          this.serverStreaming = true
          this.onStatus('connected')
        }
        this.emit(msg)
      } catch {
        /* ignore malformed frames */
      }
    }
    ws.onerror = () => {
      ws.close()
    }
    ws.onclose = () => {
      this.ws = null
      if (this.closed) {
        this.onStatus('disconnected')
        return
      }
      this.onStatus('reconnecting')
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    if (this.timer !== null || this.closed) return
    const backoff = Math.min(30_000, 1000 * 2 ** this.attempts)
    const jitter = Math.random() * 500 - 250
    this.timer = setTimeout(() => {
      this.timer = null
      this.attempts += 1
      this.open()
    }, backoff + jitter)
  }

  subscribe(symbol: string, exchange: string, interval: Interval): void {
    const sub = { symbol, exchange, interval }
    this.subs.set(symbol, sub)
    // The server keys on `type` — the wire shape is {type, symbol, exchange, interval}
    if (this.ws?.readyState === WebSocket.OPEN) this.send({ type: 'subscribe', ...sub })
  }

  unsubscribe(symbol: string): void {
    this.subs.delete(symbol)
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.send({ type: 'unsubscribe', symbol })
    }
  }

  private send(msg: object): void {
    this.ws?.send(JSON.stringify(msg))
  }

  onMessage(fn: (msg: WsMessage) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private emit(msg: WsMessage): void {
    for (const fn of this.listeners) fn(msg)
  }

  disconnect(): void {
    this.closed = true
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (this.watchdog !== null) {
      clearInterval(this.watchdog)
      this.watchdog = null
    }
    this.serverStreaming = false
    this.ws?.close()
    this.ws = null
    this.listeners.clear()
  }
}
