import { useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE } from '../api/client'
import { chartStartEpoch, chartStartIst, CHART_DAYS } from '../lib/istTime'
import { buildRangeBars, rangeSizeFromTicks } from '../lib/rangeBars'
import { clipChartDays } from './replayVisible'
import type { Candle, FetchStatus, Interval } from '../types/market'

export interface UseCandlesResult {
  candles: Candle[]
  status: FetchStatus
  source: string | null
  error: string | null
  /** Force a refetch of the current symbol/interval (used by error Retry). */
  reload: () => void
}

function clipFetched(candles: Candle[], fetchEpochS: number, days: number): Candle[] {
  const lo = chartStartEpoch(fetchEpochS, days)
  return clipChartDays(candles.filter((c) => c.time >= lo), days)
}

/**
 * Fetch historical candles for (symbol, interval).
 *
 * ``interval === 'Range'`` fetches 1m candles and converts them to price-
 * based range bars client-side. The range size is `rangeTicks` ticks when
 * given, else the auto ATR size on the contract's tick grid (never sent to
 * the wire). The wire fetch is the last `days` weekdays (default
 * `CHART_DAYS` = 3) via ``chartStartIst``, and the response is clipped to
 * the same window — so the day count flows through end-to-end and a wider
 * chart window needs no other change. The range-bar transform is a separate
 * memo keyed on the raw candles + size, so changing the size rebuilds bars
 * instantly without refetching the 1m feed.
 *
 * Race-guards: an AbortController cancels the in-flight request on symbol /
 * interval change, and a monotonically increasing request id discards any
 * stale response that still resolves.
 */
export function useCandles(
  symbol: string,
  interval: Interval,
  tickSize?: number,
  rangeTicks?: number | null,
  exchange: string = 'NFO',
  days: number = CHART_DAYS,
): UseCandlesResult {
  const [raw, setRaw] = useState<Candle[]>([])
  const [status, setStatus] = useState<FetchStatus>('loading')
  const [source, setSource] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reqId = useRef(0)
  const [reloadNonce, setReloadNonce] = useState(0)

  useEffect(() => {
    if (!symbol) {
      setRaw([])
      setStatus('empty')
      return
    }
    const id = ++reqId.current
    const ctrl = new AbortController()
    const fetchEpochS = Date.now() / 1000
    setStatus('loading')
    setError(null)

    // Same endpoint as api.candles but with an AbortController + request-id
    // guard so switching contracts can never flash stale data. Range bars
    // are derived from the 1m feed (the backend serves only timeframes).
    const wireInterval: Exclude<Interval, 'Range'> = interval === 'Range' ? '1m' : interval
    const url = new URL(`${API_BASE}/market/candles`, window.location.origin)
    url.searchParams.set('symbol', symbol)
    url.searchParams.set('interval', wireInterval)
    url.searchParams.set('exchange', exchange)
    url.searchParams.set('start', chartStartIst(fetchEpochS, days))
    fetch(url.toString(), { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return (await res.json()) as { candles: Candle[]; source: string }
      })
      .then((body) => {
        if (id !== reqId.current) return // stale response — a newer fetch won
        const clipped = clipFetched(body.candles, fetchEpochS, days)
        setRaw(clipped)
        setSource(body.source)
        setStatus(clipped.length > 0 ? 'ready' : 'empty')
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted || id !== reqId.current) return
        setError(err instanceof Error ? err.message : String(err))
        setStatus('error')
      })

    return () => ctrl.abort()
  }, [symbol, interval, exchange, days, reloadNonce])

  // Range-bar derivation — separate from the fetch so a size change rebuilds
  // the bars from the already-loaded 1m candles (no server round-trip).
  const candles = useMemo(() => {
    if (interval !== 'Range') return raw
    return buildRangeBars(raw, rangeSizeFromTicks(rangeTicks, tickSize), { tickSize })
  }, [raw, interval, rangeTicks, tickSize])

  return { candles, status, source, error, reload: () => setReloadNonce((n) => n + 1) }
}
