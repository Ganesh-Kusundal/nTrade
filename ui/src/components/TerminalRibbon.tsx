import { useEffect } from 'react'
import { Search, Radio, PlayCircle, Clock } from 'lucide-react'
import { useChartStore } from '../store/chartStore'
import { DEFAULT_EXCHANGE } from '../lib/constants'
import { fmtISTClock } from '../lib/istTime'
import { feedKind, FEED_META, isSessionOpen, type FeedKind } from '../lib/feedStatus'
import { TimeframeSelector } from './TimeframeSelector'
import { StrategyDropdown } from './StrategyDropdown'
import { SymbolSearchModal } from './SymbolSearchModal'
import { IndicatorsModal } from './IndicatorsModal'

export function TerminalRibbon({ now, catalogEpoch = 0 }: { now: number; catalogEpoch?: number }) {
  const interval = useChartStore((s) => s.interval)
  const setInterval = useChartStore((s) => s.selectInterval)
  const mode = useChartStore((s) => s.mode)
  const setMode = useChartStore((s) => s.setMode)
  const wsStatus = useChartStore((s) => s.wsStatus)
  const contract = useChartStore((s) => s.contract)
  const root = useChartStore((s) => s.root)
  const roots = useChartStore((s) => s.roots)
  const provider = useChartStore((s) => s.provider)
  const strategyId = useChartStore((s) => s.strategyId)
  const setStrategyId = useChartStore((s) => s.setStrategyId)
  const indicators = useChartStore((s) => s.indicators)
  const toggleIndicator = useChartStore((s) => s.toggleIndicator)
  const rangeTicks = useChartStore((s) => s.rangeTicks)
  const setRangeTicks = useChartStore((s) => s.setRangeTicks)
  const isSymbolSearchOpen = useChartStore((s) => s.isSymbolSearchOpen)
  const setSymbolSearchOpen = useChartStore((s) => s.setSymbolSearchOpen)
  const isIndicatorsModalOpen = useChartStore((s) => s.isIndicatorsModalOpen)
  const setIndicatorsModalOpen = useChartStore((s) => s.setIndicatorsModalOpen)
  const selectRootAndContract = useChartStore((s) => s.selectRootAndContract)

  const exchange = contract?.exchange ?? (root ? (roots.find((r) => r.root === root)?.exchange) : DEFAULT_EXCHANGE)
  const open = isSessionOpen(exchange, root, now)
  const feed: FeedKind | null = provider
    ? feedKind(mode, provider.provider, provider.live ?? false, wsStatus, wsStatus === 'stale')
    : null
  const meta = feed ? FEED_META[feed] : null

  // Hotkeys: '/' for symbol search, 'i' for indicators modal, 'l' for live, 'p' for replay
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName)) return

      if (e.key === '/' || (e.key === 'k' && (e.metaKey || e.ctrlKey))) {
        e.preventDefault()
        setSymbolSearchOpen(true)
      } else if (e.key === 'i' || e.key === 'I') {
        e.preventDefault()
        setIndicatorsModalOpen(true)
      } else if (e.key === 'l' || e.key === 'L') {
        setMode('live')
        e.preventDefault()
      } else if (e.key === 'p' || e.key === 'P') {
        setMode('replay')
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [setMode, setSymbolSearchOpen, setIndicatorsModalOpen])

  return (
    <>
      <header className="flex h-12 min-h-[48px] items-center justify-between border-b border-line/70 bg-[#0F172A] px-3">
        {/* Left Section: Brand + Symbol + Timeframe + Strategy + Mode */}
        <div className="flex items-center gap-2.5 overflow-x-auto py-1">
          {/* Brand Logo */}
          <div className="flex items-center gap-2 mr-1">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent/20 font-mono text-xs font-black text-accent ring-1 ring-accent/30">
              nT
            </span>
            <span className="hidden font-bold tracking-tight text-ink sm:inline text-sm">
              nTrade
            </span>
          </div>

          <div className="h-5 w-px bg-line/60" />

          {/* TradingView-Style Symbol Selector Button */}
          <button
            type="button"
            onClick={() => setSymbolSearchOpen(true)}
            className="group flex items-center gap-2 rounded-lg border border-line/70 bg-[#1E293B]/80 px-2.5 py-1 text-left transition-all hover:border-accent/70 hover:bg-[#1E293B]"
            title="Search symbol or expiry contract (/)"
          >
            <Search className="h-3.5 w-3.5 text-muted group-hover:text-accent" />
            <div className="flex items-center gap-1.5 font-mono text-xs">
              <span className="font-bold text-ink">{root || 'Select Symbol'}</span>
              {contract && (
                <span className="text-[11px] text-muted group-hover:text-ink">
                  {contract.symbol}
                </span>
              )}
            </div>
            {contract && (
              <span
                className={`rounded px-1.5 py-0.2 text-[9px] font-mono font-bold ${
                  contract.exchange.toUpperCase().includes('MCX')
                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                    : 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                }`}
              >
                {contract.exchange}
              </span>
            )}
            <kbd className="hidden rounded bg-panel2 px-1 py-0.2 text-[9px] font-mono text-muted group-hover:text-ink md:inline">
              /
            </kbd>
          </button>

          <div className="h-5 w-px bg-line/60" />

          {/* Timeframe Selector (Single Unified Instance) */}
          <TimeframeSelector value={interval} onChange={setInterval} />

          {/* Range Settings if interval === 'Range' */}
          {interval === 'Range' && (
            <div className="flex items-center gap-1.5 rounded-lg border border-line/60 bg-panel/70 px-2 py-0.5">
              <button
                type="button"
                className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-bold ${
                  rangeTicks == null ? 'bg-accent text-base' : 'text-muted hover:text-ink'
                }`}
                onClick={() => setRangeTicks(null)}
                title="Auto ATR sizing"
              >
                AUTO
              </button>
              <input
                type="number"
                min={1}
                className="w-14 rounded border border-line/60 bg-panel2/80 px-1.5 py-0.5 text-center font-mono text-xs text-ink outline-none"
                placeholder="ticks"
                value={rangeTicks ?? ''}
                onChange={(e) => {
                  const v = e.target.value === '' ? null : Math.round(Number(e.target.value))
                  setRangeTicks(v == null ? null : Math.max(1, v))
                }}
              />
              <span className="text-[10px] font-mono text-muted">ticks</span>
            </div>
          )}

          <div className="h-5 w-px bg-line/60" />

          {/* Strategy & Indicators Dropdown + Modal Trigger */}
          <StrategyDropdown
            strategyId={strategyId}
            onSelectStrategy={setStrategyId}
            indicators={indicators}
            onToggleIndicator={toggleIndicator}
            onOpenModal={() => setIndicatorsModalOpen(true)}
            catalogEpoch={catalogEpoch}
          />

          <div className="h-5 w-px bg-line/60" />

          {/* Mode Switcher: LIVE / REPLAY */}
          <div className="flex items-center gap-0.5 rounded-lg border border-line/60 bg-panel/70 p-0.5">
            <button
              type="button"
              onClick={() => setMode('live')}
              className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition-all ${
                mode === 'live'
                  ? 'bg-accent text-base font-bold shadow-sm'
                  : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
              title="Live streaming mode (L)"
            >
              <Radio className="h-3 w-3" />
              <span>LIVE</span>
            </button>
            <button
              type="button"
              onClick={() => setMode('replay')}
              className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition-all ${
                mode === 'replay'
                  ? 'bg-accent text-base font-bold shadow-sm'
                  : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
              title="Bar replay backtest mode (P)"
            >
              <PlayCircle className="h-3 w-3" />
              <span>REPLAY</span>
            </button>
          </div>
        </div>

        {/* Right Section: Session Status + Feed Status + IST Clock */}
        <div className="flex items-center gap-3">
          {/* Session Status Pill */}
          <span className="flex items-center gap-1.5 rounded-lg border border-line/60 bg-panel2/60 px-2 py-1">
            <span
              className={`h-1.5 w-1.5 rounded-full ${open ? 'bg-accent animate-pulse' : 'bg-slate-500'}`}
              aria-hidden
            />
            <span className={`text-[11px] font-mono font-bold ${open ? 'text-accent' : 'text-muted'}`}>
              {open ? 'SESSION OPEN' : 'CLOSED'}
            </span>
          </span>

          {/* Feed Status Pill */}
          <span
            className="hidden sm:flex items-center gap-1.5 rounded-lg border border-line/60 bg-panel2/60 px-2 py-1"
            title="Feed status"
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${meta?.dot ?? 'bg-slate-500'}`}
              aria-hidden
            />
            <span className={`text-[11px] font-mono font-medium ${meta?.text ?? 'text-muted'}`}>
              {meta?.label ?? '—'}
            </span>
          </span>

          {/* Clock */}
          <div className="flex items-center gap-1 font-mono text-xs text-ink/90 bg-panel2/50 border border-line/50 rounded-lg px-2 py-1">
            <Clock className="h-3 w-3 text-muted" />
            <span>{fmtISTClock(now)}</span>
          </div>
        </div>
      </header>

      {/* Symbol Search Modal */}
      <SymbolSearchModal
        isOpen={isSymbolSearchOpen}
        onClose={() => setSymbolSearchOpen(false)}
        roots={roots}
        currentRoot={root}
        currentContract={contract}
        onSelectContract={selectRootAndContract}
      />

      {/* TradingView Indicators & Strategies Modal */}
      <IndicatorsModal
        isOpen={isIndicatorsModalOpen}
        onClose={() => setIndicatorsModalOpen(false)}
        strategyId={strategyId}
        onSelectStrategy={setStrategyId}
        indicators={indicators}
        onToggleIndicator={toggleIndicator}
      />
    </>
  )
}
