import { useEffect, useRef, useState } from 'react'
import { API_BASE } from '../api/client'
import { chartStartEpoch, chartStartIst, CHART_DAYS } from '../lib/istTime'
import type { Candle, ChartResponse, ChartOverlays, StrategyPayload, FetchStatus, Interval } from '../types/market'

export interface UseChartResult {
  candles: Candle[]
  overlays: ChartOverlays | null
  strategy: StrategyPayload | null
  status: FetchStatus
  source: string | null
  /** Why a live broker returned no candles (distinguishes from "no history"). */
  reason: string | null
  error: string | null
  /** Force a refetch of the current symbol/interval (used by error Retry). */
  reload: () => void
}

function clipFetched(candles: Candle[], fetchEpochS: number, days: number): Candle[] {
  const lo = chartStartEpoch(fetchEpochS, days)
  return candles.filter((c) => c.time >= lo)
}

/**
 * Fetch the full chart (candles + every overlay + optional strategy markers)
 * from the backend's single calc path. The FE renders this verbatim — it does
 * not recompute VWAP / volume profile / absorptions / strategy math (the
 * zero-parity rule: chart, live WS, paper and backtest all call the same
 * Python analytics + strategy implementations).
 *
 * Live overlay patches (WS `overlays` messages) are folded in by the caller via
 * `applyOverlays`. Historical-only / server-down falls back to raw candles so
 * the chart still renders (without overlays) instead of going blank.
 */
export function useChart(
  symbol: string,
  interval: Interval,
  exchange: string = 'NFO',
  strategyId: string | null = null,
  days: number = CHART_DAYS,
  tickSize?: number | null,
  rangeTicks?: number | null,
): UseChartResult {
  const [candles, setCandles] = useState<Candle[]>([])
  const [overlays, setOverlays] = useState<ChartOverlays | null>(null)
  const [strategy, setStrategy] = useState<StrategyPayload | null>(null)
  const [status, setStatus] = useState<FetchStatus>('loading')
  const [source, setSource] = useState<string | null>(null)
  const [reason, setReason] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reqId = useRef(0)
  const [reloadNonce, setReloadNonce] = useState(0)
  const useChartTickSizeRef = useRef<number | null>(tickSize ?? null)
  const rangeTicksRef = useRef<number | null>(rangeTicks ?? null)
  useChartTickSizeRef.current = tickSize ?? null
  rangeTicksRef.current = rangeTicks ?? null

  useEffect(() => {
    if (!symbol) {
      setCandles([])
      setOverlays(null)
      setStrategy(null)
      setStatus('empty')
      return
    }
    const id = ++reqId.current
    const ctrl = new AbortController()
    const fetchEpochS = Date.now() / 1000
    setStatus('loading')
    setError(null)

    // Pass `Range` through verbatim — the backend builds price-based range
    // bars (no client derivation anymore). Range size is ticks * tick_size.
    const url = new URL(`${API_BASE}/market/chart`, window.location.origin)
    url.searchParams.set('symbol', symbol)
    url.searchParams.set('interval', interval)
    url.searchParams.set('exchange', exchange)
    url.searchParams.set('start', chartStartIst(fetchEpochS, days))
    url.searchParams.set('limit', '5000')
    if (strategyId) url.searchParams.set('strategy', strategyId)
    if (interval === 'Range') {
      const ts = useChartTickSizeRef.current
      if (ts != null) url.searchParams.set('tick_size', String(ts))
      if (rangeTicksRef.current != null && ts != null) {
        url.searchParams.set('range_size', String(rangeTicksRef.current * ts))
      }
    }

    fetch(url.toString(), { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return (await res.json()) as ChartResponse
      })
      .then((body) => {
        if (id !== reqId.current) return
        // For Range charts the backend returns 1m candles + `range_bars`; the
        // chart renders the range bars as the base series.
        const base: Candle[] = interval === 'Range' && Array.isArray(body.range_bars)
          ? (body.range_bars as Candle[])
          : body.candles
        const clipped = clipFetched(base, fetchEpochS, days)
        setCandles(clipped)
        setOverlays(body.overlays ?? null)
        setStrategy(body.strategy ?? null)
        setSource(body.source)
        setReason((body as ChartResponse).reason ?? null)
        setStatus(clipped.length > 0 ? 'ready' : 'empty')
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted || id !== reqId.current) return
        setError(err instanceof Error ? err.message : String(err))
        setStatus('error')
      })

    return () => ctrl.abort()
  }, [symbol, interval, exchange, strategyId, days, tickSize, rangeTicks, reloadNonce])

  return {
    candles,
    overlays,
    strategy,
    status,
    source,
    reason,
    error,
    reload: () => setReloadNonce((n) => n + 1),
  }
}
