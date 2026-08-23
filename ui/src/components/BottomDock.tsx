import { useEffect, useState, useCallback, useRef } from 'react'
import {
  ChevronDown,
  ChevronUp,
  Maximize2,
  Minimize2,
  TrendingUp,
  Activity,
  Wallet,
  PlayCircle,
  FileText,
  Play,
  Pause,
  RotateCcw,
  SkipForward,
  AlertCircle,
  CheckCircle2,
  Bot,
} from 'lucide-react'
import { api, type PaperStatus, type PaperPosition } from '../api/client'
import { fmtPrice, fmtExpiry } from '../lib/format'
import { strategyLabel } from '../lib/registry'
import { fmtIST } from '../lib/istTime'
import type { Contract, Quote, StrategyPayload, StrategySignal, Candle } from '../types/market'
import type { ReplayState, ReplaySpeed } from '../hooks/replayReducer'
import type { ReplayPaperAccount } from '../lib/replayPaperTrader'

type DockTab = 'positions' | 'strategy' | 'account' | 'replay' | 'contract'

interface BottomDockProps {
  quote?: Quote | null
  contract: Contract | null
  symbol: string
  exchange: string
  strategyId: string
  strategyResult: StrategyPayload | null
  mode: 'live' | 'replay'
  // Replay props if in replay mode
  replayState?: ReplayState
  replayCandles?: Candle[]
  replayAccount?: ReplayPaperAccount | null
  replayFromValue?: string
  replayToValue?: string
  onReplayFromChange?: (v: string) => void
  onReplayToChange?: (v: string) => void
  onReplayPlay?: () => void
  onReplayPause?: () => void
  onReplayResume?: () => void
  onReplaySeek?: (index: number) => void
  onReplayReset?: () => void
  onReplayJumpToLatest?: () => void
  onReplaySpeed?: (speed: ReplaySpeed) => void
}

interface SignalTrade {
  side: 'BUY' | 'SELL'
  entry: number
  sl: number | null
  tp: number | null
  exit: number | null
  exitReason: string | null
  qty: number
}

function toTrades(signals: StrategySignal[]): SignalTrade[] {
  const trades: SignalTrade[] = []
  const open: SignalTrade[] = []
  for (const s of signals) {
    if (s.exit_reason) {
      const t = open.shift()
      if (t) {
        t.exit = s.intent_price ?? s.reference_price ?? null
        t.exitReason = s.exit_reason
        trades.push(t)
      }
    } else {
      open.push({
        side: s.side,
        entry: s.reference_price ?? s.intent_price ?? 0,
        sl: s.sl ?? null,
        tp: s.tp ?? null,
        exit: null,
        exitReason: null,
        qty: s.quantity,
      })
    }
  }
  for (const t of open) trades.push(t)
  return trades
}

export function BottomDock({
  contract,
  symbol,
  exchange,
  strategyId,
  strategyResult,
  mode,
  replayState,
  replayCandles = [],
  replayAccount,
  replayFromValue,
  replayToValue,
  onReplayFromChange,
  onReplayToChange,
  onReplayPlay,
  onReplayPause,
  onReplayResume,
  onReplaySeek,
  onReplayReset,
  onReplayJumpToLatest,
  onReplaySpeed,
}: BottomDockProps) {
  const [isCollapsed, setIsCollapsed] = useState(false)
  const [isMaximized, setIsMaximized] = useState(false)
  const [activeTab, setActiveTab] = useState<DockTab>(mode === 'replay' ? 'replay' : 'positions')

  // Live Paper status
  const [paperStatus, setPaperStatus] = useState<PaperStatus | null>(null)
  const [paperBusy, setPaperBusy] = useState(false)
  const [paperError, setPaperError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const isPaperRunning = paperStatus?.running === true

  const refreshPaper = useCallback(() => {
    if (mode !== 'live') return
    api
      .paperStatus()
      .then((s) => {
        setPaperStatus(s)
        setPaperError(null)
      })
      .catch((e: unknown) => setPaperError(e instanceof Error ? e.message : String(e)))
  }, [mode])

  useEffect(() => {
    if (mode === 'live') {
      refreshPaper()
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = setInterval(refreshPaper, 2500)
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [refreshPaper, mode])

  const startPaper = async () => {
    if (!symbol) return
    setPaperBusy(true)
    try {
      const s = await api.paperStart(symbol, exchange)
      setPaperStatus(s)
      setPaperError(null)
    } catch (e: unknown) {
      setPaperError(e instanceof Error ? e.message : String(e))
    } finally {
      setPaperBusy(false)
    }
  }

  const stopPaper = async () => {
    setPaperBusy(true)
    try {
      const s = await api.paperStop()
      setPaperStatus(s)
      setPaperError(null)
    } catch (e: unknown) {
      setPaperError(e instanceof Error ? e.message : String(e))
    } finally {
      setPaperBusy(false)
    }
  }

  useEffect(() => {
    if (mode === 'replay') {
      setActiveTab('replay')
    }
  }, [mode])

  const trades = strategyResult?.signals ? toTrades(strategyResult.signals) : []
  const openTradesCount = trades.filter((t) => t.exit == null).length
  const livePositions = paperStatus?.positions ?? []
  const liveTotalPnL = (paperStatus?.realized_pnl ?? 0) + (paperStatus?.unrealized_pnl ?? 0)

  // In replay mode, use replayAccount metrics; in live mode, use live paperStatus
  const isReplay = mode === 'replay'
  const replayTotalPnL = replayAccount?.total_pnl ?? 0
  const replayEquity = replayAccount?.equity ?? 1_000_000
  const replayBalance = replayAccount?.balance ?? 1_000_000
  const replayHasOpenPos = replayAccount?.open_position != null

  const bias = strategyResult?.bias ?? null

  const REPLAY_SPEEDS: ReplaySpeed[] = [1, 2, 5, 10, 15, 20]

  return (
    <div
      className={`flex flex-col border-t border-line/70 bg-[#0F172A] transition-all duration-200 ${
        isCollapsed ? 'h-9' : isMaximized ? 'h-[440px]' : 'h-[230px]'
      }`}
    >
      {/* Dock Header / Tab Strip */}
      <div className="flex h-9 min-h-[36px] items-center justify-between border-b border-line/60 bg-[#131B2E] px-3">
        {/* Left: Tab Buttons */}
        <div className="flex items-center gap-1 overflow-x-auto">
          {/* Tab: Positions */}
          <button
            type="button"
            onClick={() => {
              setActiveTab('positions')
              if (isCollapsed) setIsCollapsed(false)
            }}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition-colors ${
              activeTab === 'positions' && !isCollapsed
                ? 'bg-panel2 text-accent font-bold border border-accent/30'
                : 'text-muted hover:bg-panel2/60 hover:text-ink'
            }`}
          >
            <TrendingUp className="h-3.5 w-3.5" />
            <span>Positions</span>
            {isReplay ? (
              replayHasOpenPos && (
                <span className="rounded bg-accent/20 px-1 py-0.2 text-[10px] font-bold text-accent">
                  1 Open
                </span>
              )
            ) : (
              livePositions.length > 0 && (
                <span className="rounded bg-accent/20 px-1 py-0.2 text-[10px] font-bold text-accent">
                  {livePositions.length}
                </span>
              )
            )}
          </button>

          {/* Tab: Strategy Signals */}
          <button
            type="button"
            onClick={() => {
              setActiveTab('strategy')
              if (isCollapsed) setIsCollapsed(false)
            }}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition-colors ${
              activeTab === 'strategy' && !isCollapsed
                ? 'bg-panel2 text-accent font-bold border border-accent/30'
                : 'text-muted hover:bg-panel2/60 hover:text-ink'
            }`}
          >
            <Activity className="h-3.5 w-3.5" />
            <span>{strategyLabel(strategyId)} Signals</span>
            {openTradesCount > 0 && (
              <span className="rounded bg-blue-500/20 px-1 py-0.2 text-[10px] font-bold text-blue-300">
                {openTradesCount} active
              </span>
            )}
          </button>

          {/* Tab: Paper Account */}
          <button
            type="button"
            onClick={() => {
              setActiveTab('account')
              if (isCollapsed) setIsCollapsed(false)
            }}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition-colors ${
              activeTab === 'account' && !isCollapsed
                ? 'bg-panel2 text-accent font-bold border border-accent/30'
                : 'text-muted hover:bg-panel2/60 hover:text-ink'
            }`}
          >
            <Wallet className="h-3.5 w-3.5" />
            <span>Paper Account {isReplay ? '(1M Capital)' : ''}</span>
            {isReplay && (
              <span className="rounded bg-emerald-500/20 px-1 py-0.2 text-[9px] font-bold text-emerald-300">
                Auto
              </span>
            )}
          </button>

          {/* Tab: Bar Replay */}
          {mode === 'replay' && (
            <button
              type="button"
              onClick={() => {
                setActiveTab('replay')
                if (isCollapsed) setIsCollapsed(false)
              }}
              className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition-colors ${
                activeTab === 'replay' && !isCollapsed
                  ? 'bg-panel2 text-accent font-bold border border-accent/30'
                  : 'text-muted hover:bg-panel2/60 hover:text-ink'
              }`}
            >
              <PlayCircle className="h-3.5 w-3.5" />
              <span>Bar Replay</span>
              <span className="rounded bg-accent/20 px-1.5 py-0.2 text-[9px] font-bold text-accent">
                1M Paper Auto
              </span>
            </button>
          )}

          {/* Tab: Contract Specs */}
          <button
            type="button"
            onClick={() => {
              setActiveTab('contract')
              if (isCollapsed) setIsCollapsed(false)
            }}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition-colors ${
              activeTab === 'contract' && !isCollapsed
                ? 'bg-panel2 text-accent font-bold border border-accent/30'
                : 'text-muted hover:bg-panel2/60 hover:text-ink'
            }`}
          >
            <FileText className="h-3.5 w-3.5" />
            <span>Contract Specs</span>
          </button>
        </div>

        {/* Right: Summary Metrics & Collapse/Maximize Controls */}
        <div className="flex items-center gap-3">
          {/* Quick PnL Badge */}
          {isReplay ? (
            <div className="flex items-center gap-2 font-mono text-xs">
              <span className="text-muted/70">Replay PnL:</span>
              <span className={`font-bold ${replayTotalPnL >= 0 ? 'text-accent' : 'text-danger'}`}>
                {replayTotalPnL >= 0 ? '+' : ''}₹
                {replayTotalPnL.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </span>
            </div>
          ) : (
            isPaperRunning &&
            paperStatus && (
              <div className="flex items-center gap-2 font-mono text-xs">
                <span className="text-muted/70">Paper PnL:</span>
                <span className={`font-bold ${liveTotalPnL >= 0 ? 'text-accent' : 'text-danger'}`}>
                  {liveTotalPnL >= 0 ? '+' : ''}₹
                  {liveTotalPnL.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                </span>
              </div>
            )
          )}

          {/* Strategy Bias Indicator */}
          {bias && (
            <div className="hidden sm:flex items-center gap-1.5 font-mono text-xs">
              <span className="text-muted/70">Bias:</span>
              <span
                className={`rounded px-1.5 py-0.2 font-bold text-[10px] ${
                  bias === 'UP'
                    ? 'bg-accent/20 text-accent'
                    : bias === 'DOWN'
                    ? 'bg-danger/20 text-danger'
                    : 'bg-amber-500/20 text-amber-300'
                }`}
              >
                {bias}
              </span>
            </div>
          )}

          <div className="h-4 w-px bg-line/60" />

          {/* Maximize Toggle */}
          {!isCollapsed && (
            <button
              type="button"
              onClick={() => setIsMaximized((prev) => !prev)}
              className="rounded p-1 text-muted hover:bg-panel2 hover:text-ink"
              title={isMaximized ? 'Restore height' : 'Maximize panel'}
            >
              {isMaximized ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            </button>
          )}

          {/* Collapse / Expand Toggle */}
          <button
            type="button"
            onClick={() => setIsCollapsed((prev) => !prev)}
            className="flex items-center gap-1 rounded bg-panel2/80 px-2 py-0.5 text-xs text-muted hover:bg-panel2 hover:text-ink font-semibold"
            title={isCollapsed ? 'Expand panel' : 'Collapse panel'}
          >
            {isCollapsed ? (
              <>
                <ChevronUp className="h-3.5 w-3.5 text-accent" />
                <span className="text-[10px]">EXPAND</span>
              </>
            ) : (
              <>
                <ChevronDown className="h-3.5 w-3.5" />
                <span className="text-[10px]">HIDE</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Dock Content Body */}
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto p-3 bg-base/50">
          {/* TAB 1: Positions */}
          {activeTab === 'positions' && (
            <div className="space-y-3">
              {isReplay ? (
                // Replay Mode Positions
                replayAccount?.open_position ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs font-mono">
                      <thead className="border-b border-line/60 text-[10px] uppercase text-muted">
                        <tr>
                          <th className="pb-2">Symbol</th>
                          <th className="pb-2">Side</th>
                          <th className="pb-2">Quantity</th>
                          <th className="pb-2">Avg Entry</th>
                          <th className="pb-2">Current LTP</th>
                          <th className="pb-2">SL Price</th>
                          <th className="pb-2">TP Price</th>
                          <th className="pb-2">Unrealized PnL</th>
                          <th className="pb-2">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line/30">
                        {(() => {
                          const p = replayAccount.open_position!
                          const isLong = p.side === 'BUY'
                          return (
                            <tr className="hover:bg-panel2/40">
                              <td className="py-2.5 font-bold text-ink">{p.symbol}</td>
                              <td className="py-2.5">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                    isLong ? 'bg-accent/20 text-accent' : 'bg-danger/20 text-danger'
                                  }`}
                                >
                                  {isLong ? 'LONG' : 'SHORT'}
                                </span>
                              </td>
                              <td className="py-2.5 font-semibold text-ink">{p.qty}</td>
                              <td className="py-2.5 text-muted">{fmtPrice(p.entry_price)}</td>
                              <td className="py-2.5 text-ink font-medium">{fmtPrice(p.current_price)}</td>
                              <td className="py-2.5 text-danger font-medium">
                                {p.sl != null ? fmtPrice(p.sl) : '—'}
                              </td>
                              <td className="py-2.5 text-accent font-medium">
                                {p.tp != null ? fmtPrice(p.tp) : '—'}
                              </td>
                              <td className="py-2.5">
                                <span
                                  className={`font-bold ${
                                    p.unrealized_pnl >= 0 ? 'text-accent' : 'text-danger'
                                  }`}
                                >
                                  {p.unrealized_pnl >= 0 ? '+' : ''}₹{p.unrealized_pnl.toFixed(2)} (
                                  {p.return_pct.toFixed(2)}%)
                                </span>
                              </td>
                              <td className="py-2.5">
                                <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold text-emerald-300 border border-emerald-500/30">
                                  ⚡ Replay Open
                                </span>
                              </td>
                            </tr>
                          )
                        })()}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center p-6 text-center text-muted">
                    <TrendingUp className="h-8 w-8 text-muted/40 mb-2" />
                    <p className="text-xs font-medium text-ink/80">No open replay position</p>
                    <p className="text-[11px] text-muted mt-0.5">
                      Auto-Trader is active against ₹1,000,000 capital. Position will open on the next
                      HalfTrend signal.
                    </p>
                  </div>
                )
              ) : (
                // Live Mode Positions
                livePositions.length === 0 ? (
                  <div className="flex flex-col items-center justify-center p-6 text-center text-muted">
                    <TrendingUp className="h-8 w-8 text-muted/40 mb-2" />
                    <p className="text-xs font-medium text-ink/80">No open paper positions</p>
                    <p className="text-[11px] text-muted mt-0.5">
                      {isPaperRunning
                        ? `Paper trading is active on ${paperStatus?.symbol}. Waiting for HalfTrend entry signal...`
                        : 'Start a Paper Trading session to execute zero-risk simulated orders.'}
                    </p>
                    {!isPaperRunning && (
                      <button
                        type="button"
                        onClick={startPaper}
                        disabled={paperBusy || !symbol}
                        className="mt-3 rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-base hover:bg-accent/90 disabled:opacity-50"
                      >
                        {paperBusy ? 'Starting...' : `Start Paper Trading ${symbol || ''}`}
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs font-mono">
                      <thead className="border-b border-line/60 text-[10px] uppercase text-muted">
                        <tr>
                          <th className="pb-2">Symbol</th>
                          <th className="pb-2">Side</th>
                          <th className="pb-2">Quantity</th>
                          <th className="pb-2">Avg Entry</th>
                          <th className="pb-2">LTP</th>
                          <th className="pb-2">Unrealized PnL</th>
                          <th className="pb-2">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line/30">
                        {livePositions.map((p: PaperPosition, idx: number) => {
                          const isLong = p.quantity > 0
                          return (
                            <tr key={idx} className="hover:bg-panel2/40">
                              <td className="py-2.5 font-bold text-ink">{p.symbol}</td>
                              <td className="py-2.5">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                    isLong ? 'bg-accent/20 text-accent' : 'bg-danger/20 text-danger'
                                  }`}
                                >
                                  {isLong ? 'LONG' : 'SHORT'}
                                </span>
                              </td>
                              <td className="py-2.5 font-semibold text-ink">{Math.abs(p.quantity)}</td>
                              <td className="py-2.5 text-muted">{fmtPrice(p.avg_price)}</td>
                              <td className="py-2.5 text-ink font-medium">{fmtPrice(p.ltp)}</td>
                              <td className="py-2.5">
                                <span className={`font-bold ${p.pnl >= 0 ? 'text-accent' : 'text-danger'}`}>
                                  {p.pnl >= 0 ? '+' : ''}₹{p.pnl.toFixed(2)}
                                </span>
                              </td>
                              <td className="py-2.5">
                                <button
                                  type="button"
                                  onClick={stopPaper}
                                  disabled={paperBusy}
                                  className="rounded bg-danger/20 border border-danger/40 px-2 py-0.5 text-[10px] font-bold text-danger hover:bg-danger/30"
                                >
                                  Flatten & Stop
                                </button>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )
              )}
            </div>
          )}

          {/* TAB 2: Strategy Signals */}
          {activeTab === 'strategy' && (
            <div className="space-y-3">
              {trades.length === 0 ? (
                <div className="flex flex-col items-center justify-center p-6 text-center text-muted">
                  <Activity className="h-8 w-8 text-muted/40 mb-2" />
                  <p className="text-xs font-medium text-ink/80">No strategy signals on current candles</p>
                  <p className="text-[11px] text-muted mt-0.5">
                    HalfTrend is scanning price action. Trend reversal signals and targets will appear here.
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs font-mono">
                    <thead className="border-b border-line/60 text-[10px] uppercase text-muted">
                      <tr>
                        <th className="pb-2">Side</th>
                        <th className="pb-2">Entry Price</th>
                        <th className="pb-2">Stop Loss</th>
                        <th className="pb-2">Target Price</th>
                        <th className="pb-2">Exit Price</th>
                        <th className="pb-2">Status / Reason</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line/30">
                      {trades.map((t, idx) => {
                        const isBuy = t.side === 'BUY'
                        const isOpen = t.exit == null
                        return (
                          <tr key={idx} className="hover:bg-panel2/40">
                            <td className="py-2.5">
                              <span
                                className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                  isBuy ? 'bg-accent/20 text-accent' : 'bg-danger/20 text-danger'
                                }`}
                              >
                                {t.side}
                              </span>
                            </td>
                            <td className="py-2.5 font-bold text-ink">{fmtPrice(t.entry)}</td>
                            <td className="py-2.5 text-danger">{t.sl != null ? fmtPrice(t.sl) : '—'}</td>
                            <td className="py-2.5 text-accent">{t.tp != null ? fmtPrice(t.tp) : '—'}</td>
                            <td className="py-2.5 text-ink">{t.exit != null ? fmtPrice(t.exit) : '—'}</td>
                            <td className="py-2.5">
                              {isOpen ? (
                                <span className="rounded bg-blue-500/20 px-1.5 py-0.5 text-[10px] font-bold text-blue-300 border border-blue-500/30">
                                  IN TRADE
                                </span>
                              ) : (
                                <span className="text-[11px] text-muted">
                                  {t.exitReason || 'CLOSED'}
                                </span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* TAB 3: Paper Account */}
          {activeTab === 'account' && (
            <div className="space-y-4">
              {/* Account Metric Cards */}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-muted">
                    {isReplay ? 'Allocated Capital' : 'Cash Balance'}
                  </div>
                  <div className="mt-1 font-mono text-base font-bold text-ink">
                    ₹
                    {isReplay
                      ? replayBalance.toLocaleString(undefined, { maximumFractionDigits: 2 })
                      : paperStatus?.balance
                      ? paperStatus.balance.toLocaleString(undefined, { maximumFractionDigits: 2 })
                      : '10,00,000.00'}
                  </div>
                </div>

                <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Total Equity</div>
                  <div className="mt-1 font-mono text-base font-bold text-ink">
                    ₹
                    {isReplay
                      ? replayEquity.toLocaleString(undefined, { maximumFractionDigits: 2 })
                      : paperStatus?.equity
                      ? paperStatus.equity.toLocaleString(undefined, { maximumFractionDigits: 2 })
                      : '10,00,000.00'}
                  </div>
                </div>

                <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-muted">
                    {isReplay ? 'Total PnL (% Return)' : 'Realized PnL'}
                  </div>
                  <div
                    className={`mt-1 font-mono text-base font-bold ${
                      (isReplay ? replayTotalPnL : paperStatus?.realized_pnl ?? 0) >= 0
                        ? 'text-accent'
                        : 'text-danger'
                    }`}
                  >
                    {(isReplay ? replayTotalPnL : paperStatus?.realized_pnl ?? 0) >= 0 ? '+' : ''}
                    ₹
                    {isReplay
                      ? replayTotalPnL.toLocaleString(undefined, { maximumFractionDigits: 2 })
                      : paperStatus?.realized_pnl
                      ? paperStatus.realized_pnl.toLocaleString(undefined, { maximumFractionDigits: 2 })
                      : '0.00'}
                    {isReplay && replayAccount && (
                      <span className="text-xs font-semibold ml-1.5">
                        ({replayAccount.return_pct >= 0 ? '+' : ''}
                        {replayAccount.return_pct.toFixed(2)}%)
                      </span>
                    )}
                  </div>
                </div>

                <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-muted">
                    {isReplay ? 'Win Rate / Fills' : 'Executed Fills'}
                  </div>
                  <div className="mt-1 font-mono text-base font-bold text-ink">
                    {isReplay && replayAccount ? (
                      <span>
                        {replayAccount.win_rate.toFixed(0)}%{' '}
                        <span className="text-xs text-muted font-normal">
                          ({replayAccount.win_trades}W / {replayAccount.loss_trades}L)
                        </span>
                      </span>
                    ) : (
                      `${paperStatus?.n_trades ?? 0} fills`
                    )}
                  </div>
                </div>
              </div>

              {/* Replay Closed Trades History Table */}
              {isReplay && replayAccount && replayAccount.trades.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-muted">
                    Replay Closed Trades Log ({replayAccount.trades.length})
                  </div>
                  <div className="overflow-x-auto rounded-lg border border-line/60 bg-panel/40">
                    <table className="w-full text-left text-xs font-mono">
                      <thead className="border-b border-line/60 text-[10px] uppercase text-muted bg-panel2/40">
                        <tr>
                          <th className="p-2">Exit Time (IST)</th>
                          <th className="p-2">Side</th>
                          <th className="p-2">Qty</th>
                          <th className="p-2">Entry Price</th>
                          <th className="p-2">Exit Price</th>
                          <th className="p-2">PnL (₹)</th>
                          <th className="p-2">Return %</th>
                          <th className="p-2">Reason</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line/30">
                        {replayAccount.trades.map((t, idx) => {
                          const isWin = t.pnl >= 0
                          return (
                            <tr key={idx} className="hover:bg-panel2/40">
                              <td className="p-2 text-muted">{fmtIST(t.exit_time)}</td>
                              <td className="p-2">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                    t.side === 'BUY'
                                      ? 'bg-accent/20 text-accent'
                                      : 'bg-danger/20 text-danger'
                                  }`}
                                >
                                  {t.side}
                                </span>
                              </td>
                              <td className="p-2 font-semibold text-ink">{t.qty}</td>
                              <td className="p-2 text-muted">{fmtPrice(t.entry_price)}</td>
                              <td className="p-2 text-ink font-medium">{fmtPrice(t.exit_price)}</td>
                              <td className="p-2">
                                <span className={`font-bold ${isWin ? 'text-accent' : 'text-danger'}`}>
                                  {isWin ? '+' : ''}₹{t.pnl.toFixed(2)}
                                </span>
                              </td>
                              <td className="p-2">
                                <span className={`font-semibold ${isWin ? 'text-accent' : 'text-danger'}`}>
                                  {isWin ? '+' : ''}{t.return_pct.toFixed(2)}%
                                </span>
                              </td>
                              <td className="p-2">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${
                                    t.exit_reason.includes('TP')
                                      ? 'bg-accent/15 text-accent'
                                      : t.exit_reason.includes('SL')
                                      ? 'bg-danger/15 text-danger'
                                      : 'bg-panel2 text-muted'
                                  }`}
                                >
                                  {t.exit_reason}
                                </span>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Action Bar */}
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line/60 bg-panel2/50 p-3">
                <div className="flex items-center gap-2">
                  {isReplay ? (
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-accent">
                      <Bot className="h-4 w-4 text-accent" />
                      Replay Auto-Trade Active (₹1,000,000 Capital) — automatically executing HalfTrend Buy/Sell signals and SL/TP
                    </span>
                  ) : isPaperRunning ? (
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-accent">
                      <CheckCircle2 className="h-4 w-4" />
                      Paper Session Active on {paperStatus?.symbol}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-muted">
                      <AlertCircle className="h-4 w-4" />
                      Paper Engine Idle
                    </span>
                  )}
                  {paperStatus?.started_at && !isReplay && (
                    <span className="text-[11px] font-mono text-muted/70">
                      (Running since {fmtIST(new Date(paperStatus.started_at).getTime() / 1000)})
                    </span>
                  )}
                </div>

                {!isReplay && (
                  <div className="flex items-center gap-2">
                    {isPaperRunning ? (
                      <button
                        type="button"
                        onClick={stopPaper}
                        disabled={paperBusy}
                        className="rounded-lg bg-danger px-3 py-1.5 text-xs font-bold text-white hover:bg-danger/90 transition-colors"
                      >
                        {paperBusy ? 'Stopping...' : 'Stop Paper Session'}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={startPaper}
                        disabled={paperBusy || !symbol}
                        className="rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-base hover:bg-accent/90 transition-colors disabled:opacity-50"
                      >
                        {paperBusy
                          ? 'Starting...'
                          : `Start Paper Trading on ${symbol || 'Selected Symbol'}`}
                      </button>
                    )}
                  </div>
                )}
              </div>

              {paperError && (
                <div className="rounded-lg border border-danger/40 bg-danger/10 p-2 text-xs text-danger">
                  Error: {paperError}
                </div>
              )}
            </div>
          )}

          {/* TAB 4: Bar Replay */}
          {activeTab === 'replay' && mode === 'replay' && replayState && (
            <div className="space-y-3">
              {/* Auto-Trader Quick Performance Strip */}
              {replayAccount && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line/60 bg-[#131B2E] px-3 py-2 text-xs font-mono">
                  <div className="flex items-center gap-2">
                    <Bot className="h-4 w-4 text-accent" />
                    <span className="font-bold text-ink">Auto-Trader (1M Capital)</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-[11px]">
                    <span>
                      Equity: <b className="text-ink">₹{replayAccount.equity.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b>
                    </span>
                    <span>·</span>
                    <span>
                      PnL:{' '}
                      <b className={replayAccount.total_pnl >= 0 ? 'text-accent' : 'text-danger'}>
                        {replayAccount.total_pnl >= 0 ? '+' : ''}₹{replayAccount.total_pnl.toFixed(2)} ({replayAccount.return_pct >= 0 ? '+' : ''}{replayAccount.return_pct.toFixed(2)}%)
                      </b>
                    </span>
                    <span>·</span>
                    <span>
                      Win Rate: <b className="text-ink">{replayAccount.win_rate.toFixed(0)}%</b> ({replayAccount.win_trades}W / {replayAccount.loss_trades}L)
                    </span>
                    {replayAccount.open_position && (
                      <>
                        <span>·</span>
                        <span className="rounded bg-accent/20 px-1.5 py-0.2 text-[10px] font-bold text-accent">
                          Holding {replayAccount.open_position.side} {replayAccount.open_position.qty} qty
                        </span>
                      </>
                    )}
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3">
                {/* Play Controls */}
                <div className="flex items-center gap-1.5">
                  {replayState.status === 'playing' ? (
                    <button
                      type="button"
                      onClick={onReplayPause}
                      className="flex items-center gap-1 rounded-lg bg-amber-500/20 border border-amber-500/40 px-3 py-1.5 text-xs font-bold text-amber-300 hover:bg-amber-500/30"
                    >
                      <Pause className="h-3.5 w-3.5" />
                      Pause (Space)
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={replayState.status === 'paused' ? onReplayResume : onReplayPlay}
                      className="flex items-center gap-1 rounded-lg bg-accent px-3 py-1.5 text-xs font-bold text-base hover:bg-accent/90"
                    >
                      <Play className="h-3.5 w-3.5" />
                      Play (Space)
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={onReplayReset}
                    className="flex items-center gap-1 rounded-lg border border-line/60 bg-panel2/60 px-2.5 py-1.5 text-xs font-medium text-ink hover:bg-panel2"
                    title="Reset to beginning"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Reset
                  </button>

                  <button
                    type="button"
                    onClick={onReplayJumpToLatest}
                    className="flex items-center gap-1 rounded-lg border border-line/60 bg-panel2/60 px-2.5 py-1.5 text-xs font-medium text-ink hover:bg-panel2"
                    title="Jump to latest bar"
                  >
                    <SkipForward className="h-3.5 w-3.5" />
                    Jump to End
                  </button>
                </div>

                {/* Speed Multipliers */}
                <div className="flex items-center gap-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-muted mr-1">
                    Speed:
                  </span>
                  {REPLAY_SPEEDS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => onReplaySpeed?.(s)}
                      className={`rounded px-2 py-1 text-xs font-mono font-semibold ${
                        replayState.speed === s
                          ? 'bg-accent text-base font-bold'
                          : 'bg-panel2/60 text-muted hover:bg-panel2 hover:text-ink'
                      }`}
                    >
                      {s}x
                    </button>
                  ))}
                </div>

                {/* Window Range Inputs */}
                <div className="flex items-center gap-2 text-xs font-mono">
                  <span className="text-muted">From:</span>
                  <input
                    type="datetime-local"
                    value={replayFromValue}
                    onChange={(e) => onReplayFromChange?.(e.target.value)}
                    className="rounded border border-line/60 bg-panel2/60 px-2 py-0.5 text-xs text-ink"
                  />
                  <span className="text-muted">To:</span>
                  <input
                    type="datetime-local"
                    value={replayToValue}
                    onChange={(e) => onReplayToChange?.(e.target.value)}
                    className="rounded border border-line/60 bg-panel2/60 px-2 py-0.5 text-xs text-ink"
                  />
                </div>
              </div>

              {/* Scrubber Range Slider */}
              {(() => {
                const steps = Math.max(1, replayState.stepsPerBar ?? 1)
                const totalBars = replayCandles.length
                const currentBar =
                  replayState.cursor <= 0
                    ? 0
                    : Math.min(Math.floor((replayState.cursor - 1) / steps) + 1, totalBars)

                return (
                  <div className="flex items-center gap-3 pt-1">
                    <input
                      type="range"
                      min={0}
                      max={totalBars}
                      value={currentBar}
                      onChange={(e) => {
                        const barNum = Number(e.target.value)
                        onReplaySeek?.(barNum * steps)
                      }}
                      className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-line/60 accent-accent"
                    />
                    <span className="font-mono text-xs text-muted whitespace-nowrap">
                      Bar {currentBar} / {totalBars}
                    </span>
                  </div>
                )
              })()}
            </div>
          )}

          {/* TAB 5: Contract Specs */}
          {activeTab === 'contract' && (
            <div className="grid grid-cols-2 gap-3 text-xs font-mono sm:grid-cols-4">
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Root Symbol</div>
                <div className="mt-1 text-sm font-bold text-ink">{contract?.root ?? '—'}</div>
              </div>
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Trading Symbol</div>
                <div className="mt-1 text-sm font-bold text-ink">{symbol || '—'}</div>
              </div>
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Exchange</div>
                <div className="mt-1 text-sm font-bold text-ink">{exchange}</div>
              </div>
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Expiry Date</div>
                <div className="mt-1 text-sm font-bold text-ink">
                  {contract?.expiry ? fmtExpiry(contract.expiry) : '—'}
                </div>
              </div>
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Lot Size</div>
                <div className="mt-1 text-sm font-bold text-ink">{contract?.lot_size ?? 1}</div>
              </div>
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Tick Size</div>
                <div className="mt-1 text-sm font-bold text-ink">{contract?.tick_size ?? 0.05}</div>
              </div>
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Days to Expiry</div>
                <div className="mt-1 text-sm font-bold text-ink">{contract?.days_to_expiry ?? '—'}</div>
              </div>
              <div className="rounded-lg border border-line/60 bg-panel/70 p-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted">Status</div>
                <div className="mt-1 text-sm font-bold text-accent">Active Contract</div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
