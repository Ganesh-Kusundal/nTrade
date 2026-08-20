import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { shallow } from 'zustand/shallow'
import { MarketSocket, api } from '../api/client'
import { ChartPanel, type IndicatorToggles } from '../components/ChartPanel'
import { BrokerErrorNotice, EmptyState, ErrorState, FeedNotice, LoadingState, ReplayReadyState } from '../components/ChartStates'
import { ContractSelector } from '../components/ContractSelector'
import { IntervalSelector } from '../components/IntervalSelector'
import { ModeToggle } from '../components/ModeToggle'
import { SymbolSelector } from '../components/SymbolSelector'
import { TerminalSidePanel } from '../components/TerminalSidePanel'
import { ReplayControls } from '../components/ReplayControls'
import { feedKind, type FeedKind } from '../lib/feedStatus'
import { useChart } from '../hooks/useChart'
import { useReplay } from '../hooks/useReplay'
import {
  lastNDays,
  mergeByTime,
  mergeLive,
  visibleBars,
  windowBars,
  type TickBarMap,
} from '../hooks/replayVisible'
import { CHART_DAYS, fmtISTInput, istInputToEpoch } from '../lib/istTime'
import { usePersistedState } from '../lib/storage'
import { useChartStore } from '../store/chartStore'
import type { Candle, ChartOverlays, StrategyPayload, WsMessage } from '../types/market'

const STRATEGY_IDS = [
  { id: 'morning_vah_val', label: 'VAH/VAL', sub: 'Mukul · 09:30–11:00' },
  { id: 'valentini', label: 'Valentini', sub: 'Fabio · VWAP + absorption' },
] as const

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-[140px] flex-1 flex-col gap-1.5 sm:flex-none">
      <span className="treadout-label">{label}</span>
      {children}
    </label>
  )
}

export const TradeScreen = memo(function TradeScreen() {
  const {
    roots, contracts, root, contract, interval, mode, quote, quoteError, provider, wsStatus,
    rootsError, contractsError, selectRoot, selectContract, selectInterval, setMode,
    setWsStatus: setWsStatusStore,
  } = useChartStore(
    (s) => ({
      provider: s.provider,
      roots: s.roots,
      contracts: s.contracts,
      root: s.root,
      contract: s.contract,
      interval: s.interval,
      mode: s.mode,
      quote: s.quote,
      quoteError: s.quoteError,
      wsStatus: s.wsStatus,
      rootsError: s.rootsError,
      contractsError: s.contractsError,
      selectRoot: s.selectRoot,
      selectContract: s.selectContract,
      selectInterval: s.selectInterval,
      setMode: s.setMode,
      setWsStatus: s.setWsStatus,
    }),
    shallow,
  )

  // --- live WebSocket -----------------------------------------------
  const socketRef = useRef<MarketSocket | null>(null)
  const [liveCandles, setLiveCandles] = useState<Candle[]>([])
  const [liveOverlays, setLiveOverlays] = useState<ChartOverlays | null>(null)
  const [liveStrategy, setLiveStrategy] = useState<StrategyPayload | null>(null)

  const symbol = contract?.symbol ?? ''
  // Strategy selection lives above the WS effect + chart fetch so the live
  // subscribe and the historical chart request both read the current id.
  const [strategyId, setStrategyId] = usePersistedState<string>('ntrade.strategy', 'morning_vah_val')
  const symbolRef = useRef(symbol)
  const modeRef = useRef(mode)
  const strategyRef = useRef(strategyId)
  symbolRef.current = symbol
  modeRef.current = mode
  strategyRef.current = strategyId

  useEffect(() => {
    const s = new MarketSocket()
    socketRef.current = s
    s.onStatus = (status) => {
      setWsStatusStore(status)
    }
    const flush = (c: Candle) => setLiveCandles((prev) => mergeLive(prev, c))
    s.onMessage((msg: WsMessage) => {
      if (msg.type !== 'candle' && msg.type !== 'overlays') return
      if (msg.symbol !== symbolRef.current) return
      if (msg.type === 'candle' && modeRef.current === 'live') {
        flush(msg.candle)
      } else if (msg.type === 'overlays' && modeRef.current === 'live') {
        // Same backend pipeline as /api/market/chart — fold in live patches so
        // VWAP/VP/absorptions/strategy markers stay in lockstep with the feed.
        setLiveOverlays(msg.overlays ?? null)
        setLiveStrategy(msg.strategy ?? null)
      }
    })
    s.connect()
    return () => {
      s.disconnect()
      socketRef.current = null
    }
  }, [])

  useEffect(() => {
    if (mode !== 'live' || !symbol || interval === 'Range') return
    socketRef.current?.subscribe(symbol, contract?.exchange ?? 'NFO', interval, strategyRef.current, contract?.tick_size)
    return () => socketRef.current?.unsubscribe(symbol)
  }, [mode, symbol, interval, contract?.exchange, contract?.tick_size, strategyId])

  useEffect(() => {
    setLiveCandles([])
    setLiveOverlays(null)
    setLiveStrategy(null)
  }, [symbol, interval, mode])

  // --- historical chart (candles + every overlay + strategy) ----------
  // Fetched from the backend's single calc path (/api/market/chart). The FE
  // renders it verbatim — VWAP / volume profile / absorptions / strategy math
  // is owned by the backend (zero-parity with paper/live). Range bars are
  // built server-side too, so no client derivation remains.
  const chartDays = CHART_DAYS
  const [rangeTicks, setRangeTicks] = usePersistedState<number | null>('ntrade.rangeTicks', null)
  const { candles, overlays, strategy, status, error, reason, source, reload } = useChart(
    symbol, interval, contract?.exchange ?? 'NFO', strategyId, chartDays,
    contract?.tick_size, rangeTicks,
  )
  const displayAll = useMemo(() => mergeByTime(candles, liveCandles), [candles, liveCandles])

  const [ticksByBar, setTicksByBar] = useState<TickBarMap>(new Map())
  const [tickSeconds, setTickSeconds] = useState(0)
  useEffect(() => {
    if (mode !== 'replay' || interval === 'Range' || !symbol || candles.length === 0) {
      setTicksByBar(new Map())
      setTickSeconds(0)
      return
    }
    let cancelled = false
    setTicksByBar(new Map())
    setTickSeconds(0)
    api.ticks(symbol, interval, contract?.exchange ?? 'NFO', {
      start: fmtISTInput(candles[0].time),
      end: fmtISTInput(candles[candles.length - 1].time),
    })
      .then((res) => {
        if (cancelled) return
        const map: TickBarMap = new Map()
        for (const b of res.bars) map.set(b.time, b)
        setTicksByBar(map)
        setTickSeconds(res.seconds)
      })
      .catch(() => {
        if (!cancelled) {
          setTicksByBar(new Map())
          setTickSeconds(0)
        }
      })
    return () => {
      cancelled = true
    }
  }, [mode, symbol, interval, candles[0]?.time, candles[candles.length - 1]?.time, contract?.exchange])

  const stepsPerBar = tickSeconds > 0 ? tickSeconds : 1

  const [windowRange, setWindowRange] = usePersistedState<{ from: number; to: number } | null>('ntrade.replayWindow', null)
  const lastKeyRef = useRef<string | null>(null)
  useEffect(() => {
    const key = `${symbol}|${interval}`
    if (lastKeyRef.current == null) {
      if (symbol) lastKeyRef.current = key
      return
    }
    if (key === lastKeyRef.current) return
    lastKeyRef.current = key
    setWindowRange(null)
    setRangeTicks(null)
  }, [symbol, interval])
  const dataFrom = displayAll[0]?.time ?? 0
  const dataTo = displayAll[displayAll.length - 1]?.time ?? 0
  const defaultWindow = useMemo(() => lastNDays(displayAll, chartDays), [displayAll, chartDays])
  const effectiveWindow = useMemo(() => {
    if (mode === 'live') return defaultWindow
    if (!windowRange) return defaultWindow
    if (displayAll.length === 0) return windowRange
    const from = Math.min(Math.max(windowRange.from, dataFrom), dataTo)
    const to = Math.max(Math.min(windowRange.to, dataTo), from)
    return { from, to }
  }, [mode, windowRange, defaultWindow, displayAll, dataFrom, dataTo])
  const windowed = useMemo(
    () => windowBars(displayAll, effectiveWindow),
    [displayAll, effectiveWindow],
  )

  const replay = useReplay(windowed.length, stepsPerBar)

  useEffect(() => {
    if (mode === 'replay') replay.dispatch({ type: 'reset' })
  }, [mode, symbol, interval, windowRange])

  // --- indicator toggles --------------------------------------------
  const [indicatorsToggles, setIndicators] = usePersistedState<IndicatorToggles>('ntrade.indicators', {
    vwap: true,
    volumeProfile: true,
    absorptions: true,
    strategy: true,
  })

  const displayed = mode === 'replay'
    ? visibleBars(windowed, replay.state.cursor, stepsPerBar, ticksByBar)
    : windowed

  // Liveness gate: in live mode the strategy overlay is only trusted while
  // the feed is genuinely streaming. Stale/synthetic/offline candles must not
  // silently drive it — but the historical chart (server overlays from the
  // last load) still renders so the user never sees a blank screen.
  const feed: FeedKind | null = provider
    ? feedKind(mode, provider.provider, provider.live ?? false, wsStatus, wsStatus === 'stale')
    : null
  const showFeedNotice = mode === 'live' && provider != null && feed !== 'streaming'

  // Strategy markers come from the backend (single calc path). In live mode,
  // prefer the latest WS overlay patch once streaming; otherwise the
  // historical chart payload. While the feed is NOT streaming we still show
  // the markers from the last chart load (display-only), never recompute them.
  const strategyResult: StrategyPayload | null =
    mode === 'live' ? (liveStrategy ?? strategy) : strategy

  useEffect(() => {
    if (mode !== 'replay') return
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== 'Space') return
      const t = e.target as HTMLElement | null
      if (t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return
      e.preventDefault()
      if (replay.state.status === 'playing') replay.pause()
      else if (replay.state.status === 'paused') replay.resume()
      else if (replay.state.status !== 'completed') replay.play()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mode, replay])

  const showReplayReady =
    mode === 'replay' && status === 'ready' && replay.state.cursor === 0 &&
    (replay.state.status === 'idle' || replay.state.status === 'paused')

  const exchange = contract?.exchange ?? 'NFO'

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      {/* instrument band */}
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Root Symbol">
          <SymbolSelector
            roots={roots}
            selected={root}
            onSelect={selectRoot}
            disabled={roots.length === 0}
          />
        </Field>
        <Field label="Contract / Expiry">
          <div className="flex items-start gap-2">
            <ContractSelector
              contracts={contracts}
              selected={symbol}
              onChange={selectContract}
            />
            {symbol && (
              <span className="mt-0.5 rounded-md border border-line/60 bg-panel2/60 px-2 py-1 text-[11px] font-mono text-muted whitespace-nowrap">
                {contract?.expiry != null ? `${fmtExpiry(contract.expiry)}` : ''}
                {contract?.is_front_month && !contract?.is_expired ? ' · Front' : ''}
                {contract?.is_expired ? ' · Expired' : ''}
                {' '}· Lot {contract?.lot_size ?? '—'} · Tick {contract?.tick_size ?? '—'}
              </span>
            )}
          </div>
        </Field>
        <Field label="Interval">
          <IntervalSelector value={interval} onChange={selectInterval} />
        </Field>
        {interval === 'Range' && (
          <Field label="Range Size">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                className={`tpill ${rangeTicks == null ? 'tpill-active' : ''}`}
                onClick={() => setRangeTicks(null)}
                aria-pressed={rangeTicks == null}
                title="Auto size from ATR(14), rounded to the tick grid"
              >
                Auto
              </button>
              <input
                type="number"
                min={1}
                className="tinput w-24"
                placeholder="ticks"
                aria-label="Range size in ticks"
                value={rangeTicks ?? ''}
                onChange={(e) => {
                  const v = e.target.value === '' ? null : Math.round(Number(e.target.value))
                  setRangeTicks(v == null ? null : Math.max(1, v))
                }}
              />
              <span className="text-[11px] text-muted/60">
                {rangeTicks != null && contract?.tick_size
                  ? `≈ ${(rangeTicks * contract.tick_size).toLocaleString(undefined, { maximumFractionDigits: 2 })} pts`
                  : 'auto'}
              </span>
            </div>
          </Field>
        )}
        {mode === 'live' && interval === 'Range' && (
          <span className="rounded-md border border-line/60 bg-panel2/60 px-2 py-1 text-[11px] text-muted">
            Range is historical — no live updates
          </span>
        )}
        {source && (
          <span className="rounded-md border border-line/60 bg-panel2/60 px-2 py-1 text-[11px] font-mono text-muted/70" title="Candle data provenance">
            {source}
          </span>
        )}

        <div className="flex items-center gap-2">
          {/* strategy selector */}
          <div className="flex items-center gap-1.5">
            <span className="treadout-label">Strategy</span>
            <div className="flex items-center gap-1">
              {STRATEGY_IDS.map(({ id, label, sub }) => (
                <button
                  key={id}
                  type="button"
                  className={`rounded-md border text-xs leading-none transition-colors duration-150 ${
                    strategyId === id
                      ? 'border-accent/80 bg-accent/15 text-accent ring-1 ring-accent/40'
                      : 'border-line/60 bg-panel2/60 text-muted hover:border-line hover:text-ink'
                  }`}
                  onClick={() => setStrategyId(id)}
                  aria-pressed={strategyId === id}
                  title={sub}
                >
                  <span className="block font-semibold">{label}</span>
                  <span className="block text-[9px] leading-tight opacity-70">{sub}</span>
                </button>
              ))}
            </div>
            {strategyId !== 'morning_vah_val' && (
              <span className="text-[10px] text-muted/70 ml-1" title="Paper trading only supports Morning VAH/VAL on the backend">
                paper: VAH/VAL
              </span>
            )}
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2 pb-0.5">
          <ModeToggle mode={mode} onChange={setMode} />
        </div>
      </div>

      {(rootsError || contractsError) && (
        <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          {rootsError ?? contractsError}
        </div>
      )}

      {/* chart + side panel */}
      <div className="flex min-h-0 gap-3 flex-1">
        <div className="relative min-w-0 flex-1 overflow-hidden">
          <ChartPanel
            candles={displayed}
            context={mode === 'replay' ? windowed : undefined}
            indicators={indicatorsToggles}
            onIndicators={(key) =>
              setIndicators((s) => ({ ...s, [key]: !s[key] }))
            }
            overlays={mode === 'live' ? (liveOverlays ?? overlays) : overlays}
            strategy={strategyResult}
            symbol={contract?.symbol}
            interval={interval}
            exchange={exchange}
            root={contract?.root}
            className="h-full w-full"
          />
          {status === 'loading' && <LoadingState label={`Loading ${interval} candles…`} />}
          {status === 'error' && <ErrorState message={error ?? 'Unknown error'} onRetry={reload} />}
          {status === 'empty' && liveCandles.length === 0 && (
            reason
              ? <EmptyState
                  title="No data from broker"
                  hint={reason}
                />
              : <EmptyState
                  title="No candles for this contract"
                  hint="The provider returned no data for the selected contract and interval. Try another interval or contract."
                />
          )}
          {showFeedNotice && status !== 'loading' && (
            <FeedNotice
              label={feed === 'stale' ? 'Live feed stale — waiting for ticks…' : 'Live feed unavailable — historical only'}
            />
          )}
          {quoteError && status !== 'loading' && (
            <BrokerErrorNotice message={quoteError} />
          )}
          {showReplayReady && <ReplayReadyState />}
        </div>
        <TerminalSidePanel
          quote={quote}
          contract={contract}
          symbol={symbol}
          exchange={exchange}
          strategyId={strategyId}
          strategyResult={strategyResult}
          mode={mode}
        />
      </div>

      {/* replay control bar */}
      {mode === 'replay' && (
        <ReplayControls
          state={replay.state}
          candles={windowed}
          fromValue={fmtISTInput(effectiveWindow?.from ?? dataFrom)}
          toValue={fmtISTInput(effectiveWindow?.to ?? dataTo)}
          onFromChange={(v) => {
            const from = istInputToEpoch(v)
            if (from == null) return
            const to = windowRange?.to ?? dataTo
            setWindowRange({ from: Math.min(Math.max(from, dataFrom), to), to })
          }}
          onToChange={(v) => {
            const to = istInputToEpoch(v)
            if (to == null) return
            const from = windowRange?.from ?? dataFrom
            setWindowRange({ from, to: Math.max(Math.min(to, dataTo), from) })
          }}
          onPlay={replay.play}
          onPause={replay.pause}
          onResume={replay.resume}
          onSeek={replay.seek}
          onReset={replay.reset}
          onJumpToLatest={replay.jumpToLatest}
          onSpeed={replay.setSpeed}
        />
      )}
    </div>
  )
})

function fmtExpiry(iso: string | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-').map(Number)
  const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
  return `${d} ${MONTHS[m - 1]} ${String(y).slice(2)}`
}
