import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { shallow } from 'zustand/shallow'
import { MarketSocket, api } from '../api/client'
import { ChartPanel } from '../components/ChartPanel'
import {
  BrokerErrorNotice,
  EmptyState,
  ErrorState,
  FeedNotice,
  LoadingState,
  ReplayReadyState,
} from '../components/ChartStates'
import { TradingViewSidebar } from '../components/TradingViewSidebar'
import { BottomDock } from '../components/BottomDock'
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
import { DEFAULT_EXCHANGE } from '../lib/constants'
import { usePersistedState } from '../lib/storage'
import { useChartStore } from '../store/chartStore'
import { simulateReplayPaperTrades, INITIAL_REPLAY_CAPITAL } from '../lib/replayPaperTrader'
import type { Candle, ChartOverlays, StrategyPayload, WsMessage } from '../types/market'

export const TradeScreen = memo(function TradeScreen({
  catalogEpoch: _catalogEpoch,
}: {
  catalogEpoch?: number
} = {}) {
  const {
    contract,
    interval,
    mode,
    quote,
    quoteError,
    provider,
    wsStatus,
    rootsError,
    contractsError,
    strategyId,
    indicators,
    toggleIndicator,
    rangeTicks,
    setWsStatus: setWsStatusStore,
  } = useChartStore(
    (s) => ({
      provider: s.provider,
      contract: s.contract,
      interval: s.interval,
      mode: s.mode,
      quote: s.quote,
      quoteError: s.quoteError,
      wsStatus: s.wsStatus,
      rootsError: s.rootsError,
      contractsError: s.contractsError,
      strategyId: s.strategyId,
      indicators: s.indicators,
      toggleIndicator: s.toggleIndicator,
      rangeTicks: s.rangeTicks,
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
        setLiveOverlays(msg.overlays ?? null)
        setLiveStrategy(msg.strategy ?? null)
      }
    })
    s.connect()
    return () => {
      s.disconnect()
      socketRef.current = null
    }
  }, [setWsStatusStore])

  useEffect(() => {
    if (mode !== 'live' || !symbol || interval === 'Range') return
    socketRef.current?.subscribe(
      symbol,
      contract?.exchange ?? DEFAULT_EXCHANGE,
      interval,
      strategyRef.current,
      contract?.tick_size,
    )
    return () => socketRef.current?.unsubscribe(symbol)
  }, [mode, symbol, interval, contract?.exchange, contract?.tick_size, strategyId])

  useEffect(() => {
    setLiveCandles([])
    setLiveOverlays(null)
    setLiveStrategy(null)
  }, [symbol, interval, mode])

  const chartDays = CHART_DAYS
  const { candles, overlays, strategy, status, error, reason, reload } = useChart(
    symbol,
    interval,
    contract?.exchange ?? DEFAULT_EXCHANGE,
    strategyId,
    chartDays,
    contract?.tick_size,
    rangeTicks,
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
    api
      .ticks(symbol, interval, contract?.exchange ?? DEFAULT_EXCHANGE, {
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
  }, [mode, symbol, interval, candles, contract?.exchange])

  const stepsPerBar = tickSeconds > 0 ? tickSeconds : 1

  const [windowRange, setWindowRange] = usePersistedState<{ from: number; to: number } | null>(
    'ntrade.replayWindow',
    null,
  )
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
  }, [symbol, interval, setWindowRange])

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

  const displayed =
    mode === 'replay'
      ? visibleBars(windowed, replay.state.cursor, stepsPerBar, ticksByBar)
      : windowed

  const feed: FeedKind | null = provider
    ? feedKind(mode, provider.provider, provider.live ?? false, wsStatus, wsStatus === 'stale')
    : null
  const showFeedNotice = mode === 'live' && provider != null && feed !== 'streaming'

  const strategyResult: StrategyPayload | null =
    mode === 'live' ? (liveStrategy ?? strategy) : strategy

  // Replay paper trading account simulation against 1M capital
  const replayAccount = useMemo(() => {
    if (mode !== 'replay' || !symbol) return null
    return simulateReplayPaperTrades(
      displayed,
      strategyResult?.markers,
      symbol,
      contract?.lot_size ?? 1,
      INITIAL_REPLAY_CAPITAL,
    )
  }, [mode, displayed, strategyResult?.markers, symbol, contract?.lot_size])

  // Keyboard shortcut for replay play/pause
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
    mode === 'replay' &&
    status === 'ready' &&
    replay.state.cursor === 0 &&
    (replay.state.status === 'idle' || replay.state.status === 'paused')

  const exchange = contract?.exchange ?? DEFAULT_EXCHANGE

  return (
    <div className="flex h-full w-full min-h-0 flex-col overflow-hidden bg-base">
      {/* Error notices */}
      {(rootsError || contractsError) && (
        <div className="border-b border-danger/40 bg-danger/10 px-3 py-1.5 text-xs text-danger">
          {rootsError ?? contractsError}
        </div>
      )}

      {/* Main Workspace: Left TV Toolbar + Central Chart */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left TradingView tool rail */}
        <TradingViewSidebar />

        {/* Central Chart Area */}
        <main className="relative flex flex-1 min-h-0 min-w-0 flex-col overflow-hidden">
          <div className="relative flex-1 min-h-0 w-full overflow-hidden">
            <ChartPanel
              candles={displayed}
              indicators={indicators}
              onIndicators={toggleIndicator}
              overlays={mode === 'live' ? (liveOverlays ?? overlays) : overlays}
              strategy={strategyResult}
              replayPosition={replayAccount?.open_position}
              symbol={contract?.symbol}
              interval={interval}
              exchange={exchange}
              root={contract?.root}
              className="h-full w-full"
            />
            {status === 'loading' && <LoadingState label={`Loading ${interval} candles…`} />}
            {status === 'error' && (
              <ErrorState message={error ?? 'Unknown error'} onRetry={reload} />
            )}
            {status === 'empty' && liveCandles.length === 0 && (
              reason ? (
                <EmptyState title="No data from broker" hint={reason} />
              ) : (
                <EmptyState
                  title="No candles for this contract"
                  hint="The provider returned no data for the selected contract and interval. Try another interval or contract."
                />
              )
            )}
            {showFeedNotice && status !== 'loading' && (
              <FeedNotice
                label={
                  feed === 'stale'
                    ? 'Live feed stale — waiting for ticks…'
                    : 'Live feed unavailable — historical only'
                }
              />
            )}
            {quoteError && status !== 'loading' && <BrokerErrorNotice message={quoteError} />}
            {showReplayReady && <ReplayReadyState />}
          </div>
        </main>
      </div>

      {/* TradingView Bottom Dock: Collapsible Position Management, Strategy Signals, Paper Trading, Replay */}
      <BottomDock
        quote={quote}
        contract={contract}
        symbol={symbol}
        exchange={exchange}
        strategyId={strategyId}
        strategyResult={strategyResult}
        mode={mode}
        replayState={mode === 'replay' ? replay.state : undefined}
        replayCandles={windowed}
        replayAccount={replayAccount}
        replayFromValue={fmtISTInput(effectiveWindow?.from ?? dataFrom)}
        replayToValue={fmtISTInput(effectiveWindow?.to ?? dataTo)}
        onReplayFromChange={(v) => {
          const from = istInputToEpoch(v)
          if (from == null) return
          const to = windowRange?.to ?? dataTo
          setWindowRange({ from: Math.min(Math.max(from, dataFrom), to), to })
        }}
        onReplayToChange={(v) => {
          const to = istInputToEpoch(v)
          if (to == null) return
          const from = windowRange?.from ?? dataFrom
          setWindowRange({ from, to: Math.max(Math.min(to, dataTo), from) })
        }}
        onReplayPlay={replay.play}
        onReplayPause={replay.pause}
        onReplayResume={replay.resume}
        onReplaySeek={replay.seek}
        onReplayReset={replay.reset}
        onReplayJumpToLatest={replay.jumpToLatest}
        onReplaySpeed={replay.setSpeed}
      />
    </div>
  )
})
