import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { shallow } from 'zustand/shallow'
import { MarketSocket, api } from '../api/client'
import { ChartPanel, type IndicatorToggles, type StrategyOverlay } from '../components/ChartPanel'
import { EmptyState, ErrorState, LoadingState, ReplayReadyState } from '../components/ChartStates'
import { ContractSelector } from '../components/ContractSelector'
import { IntervalSelector } from '../components/IntervalSelector'
import { MarketHeader } from '../components/MarketHeader'
import { ModeToggle } from '../components/ModeToggle'
import { PaperTradeControl } from '../components/PaperTradeControl'
import { ReplayControls } from '../components/ReplayControls'
import { SymbolSelector } from '../components/SymbolSelector'
import { MORNING_VAH_VAL_TUNED } from '../lib/morningVahVal'
import { strategies } from '../lib/registry'
import { strategySession } from '../lib/marketHours'
import { useCandles } from '../hooks/useCandles'
import { useReplay } from '../hooks/useReplay'
import {
  coalesceLatest,
  inProgressBar,
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
import type { Candle, WsMessage } from '../types/market'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-[140px] flex-1 flex-col gap-1 sm:flex-none">
      <span className="treadout-label">{label}</span>
      {children}
    </label>
  )
}

export const TradeScreen = memo(function TradeScreen() {
  const {
    provider, roots, contracts, root, contract, interval, mode, quote,
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
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected' | 'reconnecting' | 'off'>('disconnected')
  const [liveCandles, setLiveCandles] = useState<Candle[]>([])

  const symbol = contract?.symbol ?? ''
  const symbolRef = useRef(symbol)
  const modeRef = useRef(mode)
  symbolRef.current = symbol
  modeRef.current = mode

  useEffect(() => {
    const s = new MarketSocket()
    socketRef.current = s
    s.onStatus = (status) => {
      setWsStatus(status) // local state (TradeScreen consumers)
      setWsStatusStore(status) // mirrored to the store for the chrome
    }
    const flush = coalesceLatest<Candle>((c) => setLiveCandles((prev) => mergeLive(prev, c)))
    s.onMessage((msg: WsMessage) => {
      if (msg.type === 'candle' && msg.symbol === symbolRef.current && modeRef.current === 'live') {
        flush(msg.candle)
      }
    })
    s.connect()
    return () => {
      s.disconnect()
      socketRef.current = null
    }
  }, [])

  // Subscribe when in live mode; never double-subscribe (MarketSocket dedupes).
  // Range bars are derived from 1m — a live 1m push would need re-bucketing,
  // so Range is historical-only (live shows the built bars as-is).
  useEffect(() => {
    if (mode !== 'live' || !symbol || interval === 'Range') return
    socketRef.current?.subscribe(symbol, contract?.exchange ?? 'NFO', interval)
    return () => socketRef.current?.unsubscribe(symbol)
  }, [mode, symbol, interval, contract?.exchange])

  // Clear live candles on contract/interval change, and when leaving live
  // mode — replay must re-run over the pure historical dataset.
  useEffect(() => {
    setLiveCandles([])
  }, [symbol, interval, mode])

  // --- historical candles + replay ----------------------------------
  // `chartDays` is the single knob for how much history the chart loads and
  // windows to (default: the last 3 weekdays). It flows into the wire `start`,
  // the client-side clip, and the default replay window, so loading more days
  // is a one-line change here (plus a provider that can serve the window).
  const chartDays = CHART_DAYS
  // Range-bar size in ticks (null = auto ATR). Rebuilding is client-side and
  // cheap — useCandles re-derives bars from the cached 1m feed, no refetch.
  // Persisted so a reload restores a Range session exactly.
  const [rangeTicks, setRangeTicks] = usePersistedState<number | null>('ntrade.rangeTicks', null)
  const { candles, status, source, error, reload } = useCandles(
    symbol, interval, contract?.tick_size, rangeTicks, contract?.exchange ?? 'NFO', chartDays,
  )
  const displayAll = useMemo(() => mergeByTime(candles, liveCandles), [candles, liveCandles])

  // Tick replay: fetch synthesized 1-second ticks for the loaded window
  // (compact per-bar arrays — already grouped by bar time server-side). A
  // failure or empty response (e.g. the range exceeds the tick budget) falls
  // back to plain bar replay — stepsPerBar 1. Deterministic per bar.
  const [ticksByBar, setTicksByBar] = useState<TickBarMap>(new Map())
  const [tickSeconds, setTickSeconds] = useState(0)
  // Range bars span multiple 1m candles, so per-1m ticks don't map — replay
  // falls back to plain bar-by-bar playback (stepsPerBar 1).
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

  // Replay window: when set, replay plays only this slice of history. Reset on
  // a *user* dataset (symbol/interval) change so a new contract starts from the
  // default; the initial hydration (reload restores the persisted window) is
  // left alone. The default (no user override) is the loaded chart window
  // (`chartDays` weekdays).
  const [windowRange, setWindowRange] = usePersistedState<{ from: number; to: number } | null>('ntrade.replayWindow', null)
  // Last-seen dataset key — skip the reset for the first real dataset (the
  // persisted window must survive a reload), reset for every later change
  // (including returning to a previously-seen interval).
  const lastKeyRef = useRef<string | null>(null)
  useEffect(() => {
    const key = `${symbol}|${interval}`
    if (lastKeyRef.current == null) {
      if (symbol) lastKeyRef.current = key // first real dataset — keep the persisted window
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
  // A persisted window may outlive the data it was saved against (different
  // contract after a reload) — clamp it into the loaded range so replay never
  // shows an empty window.
  const effectiveWindow = useMemo(() => {
    // Live always shows the loaded chart window — persisted replay ranges
    // must not widen the chart after switching back from replay.
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

  // Entering replay (or switching contract/interval/window while replaying)
  // restarts at the window start. Not keyed on stepsPerBar: tick data arriving
  // late must clamp the cursor (load already does), not rewind a play in
  // progress back to zero.
  useEffect(() => {
    if (mode === 'replay') replay.dispatch({ type: 'reset' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, symbol, interval, windowRange])

  // Strategy indicator overlays — all on by default, toggleable. Persisted
  // so a reload restores the exact overlay set. Defaults come from the
  // ``strategies`` registry so adding a new strategy only requires registering
  // its prerequisites (no edit here).
  const [indicators, setIndicators] = usePersistedState<IndicatorToggles>('ntrade.indicators', {
    vwap: true,
    volumeProfile: true,
    absorptions: true,
    strategy: true,
  })

  const displayed = mode === 'replay'
    ? visibleBars(windowed, replay.state.cursor, stepsPerBar, ticksByBar)
    : windowed
  const replayBar = mode === 'replay'
    ? inProgressBar(windowed, replay.state.cursor, stepsPerBar, ticksByBar)
    : null

  // Fabio strategy on completed bars only. Keyed on closed-bar count + last
  // closed time so an in-progress live tick does not rerun the state machine.
  const strategyCount = mode === 'replay'
    ? Math.max(0, Math.floor(replay.state.cursor / Math.max(1, stepsPerBar)))
    : liveCandles.length > 0 ? Math.max(0, windowed.length - 1) : windowed.length
  const strategyHeadTime = windowed[strategyCount - 1]?.time ?? 0
  const strategyCandles = useMemo(
    () => windowed.slice(0, strategyCount),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- windowed read on count/time change only
    [mode, strategyCount, strategyHeadTime],
  )
  // Morning VAH/VAL scalper (Mukul Chowdhury "first 15 minutes" setup) on
  // completed bars — buy/sell signals on the chart by default. The session
  // end keeps the MCX evening contracts honest; the tradeable window itself
  // stays 09:30–11:00 IST. Run through the registry so the strategy spec
  // (label, default params, prerequisite indicators) is the single source of
  // truth and TradeScreen never imports `runMorningVahVal` / TUNED directly.
  const strategyResult = useMemo(
    () => {
      const session = strategySession(contract?.exchange, contract?.root ?? root)
      const spec = strategies['morning_vah_val']
      const base = { ...(spec.defaultParams ?? {}), ...MORNING_VAH_VAL_TUNED }
      return spec.run(strategyCandles, {
        sessionStart: session.start,
        sessionEnd: session.end,
        ...base,
      }) as StrategyOverlay
    },
    [strategyCandles, contract?.exchange, contract?.root, root],
  )
  const lastClose = displayed.length > 0 ? displayed[displayed.length - 1].close : null

  // Space toggles replay play/pause when not typing in a field.
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

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      {/* selection + header band */}
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
          <ContractSelector
            contracts={contracts}
            selected={symbol}
            onChange={selectContract}
          />
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
          <span
            className="rounded-md border border-line/60 bg-panel2/60 px-2 py-1 text-[11px] text-muted"
            title="Range bars are derived from the 1m feed client-side, so they are historical-only — no live updates."
          >
            Range is historical — no live updates
          </span>
        )}
        <div className="ml-auto flex items-center gap-2 pb-0.5">
          <PaperTradeControl
            symbol={symbol}
            exchange={contract?.exchange ?? 'NFO'}
            disabled={!symbol}
          />
          <ModeToggle mode={mode} onChange={setMode} />
        </div>
      </div>

      {(rootsError || contractsError) && (
        <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          {rootsError ?? contractsError}
        </div>
      )}

      <MarketHeader
        contract={contract}
        quote={quote}
        lastClose={lastClose}
        source={source}
        live={provider?.live ?? false}
        wsStatus={wsStatus}
        mode={mode}
        replayBar={replayBar}
      />

      {/* chart area — owns the viewport; replay controls pin to its bottom */}
      <div className="tpanel relative min-h-0 flex-1 overflow-hidden">
        <ChartPanel
          candles={displayed}
          context={mode === 'replay' ? windowed : undefined}
          indicators={indicators}
          onIndicators={(key) => setIndicators((s) => ({ ...s, [key]: !s[key] }))}
          strategyCandles={strategyCandles}
          strategy={strategyResult}
          symbol={contract?.symbol}
          interval={interval}
          exchange={contract?.exchange}
          root={contract?.root}
          className="h-full w-full"
        />
        {status === 'loading' && <LoadingState label={`Loading ${interval} candles…`} />}
        {status === 'error' && <ErrorState message={error ?? 'Unknown error'} onRetry={reload} />}
        {status === 'empty' && liveCandles.length === 0 && (
          <EmptyState
            title="No candles for this contract"
            hint="The provider returned no data for the selected contract and interval. Try another interval or contract."
          />
        )}
        {showReplayReady && <ReplayReadyState />}
      </div>

      {/* replay control bar — pinned under the chart, only in replay */}
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
