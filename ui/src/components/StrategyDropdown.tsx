import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Activity, Eye, EyeOff, Sparkles, Check, Layers, LayoutGrid } from 'lucide-react'
import type { IndicatorToggles } from './ChartPanel'
import { strategies, strategyLabel } from '../lib/registry'

interface StrategyDropdownProps {
  strategyId: string
  onSelectStrategy: (id: string) => void
  indicators: IndicatorToggles
  onToggleIndicator: (key: keyof IndicatorToggles) => void
  onOpenModal?: () => void
  disabled?: boolean
  /** Bumped after ``refreshCatalog()`` merges the backend registry. */
  catalogEpoch?: number
}

export function StrategyDropdown({
  strategyId,
  onSelectStrategy,
  indicators,
  onToggleIndicator,
  onOpenModal,
  disabled,
  catalogEpoch = 0,
}: StrategyDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement | null>(null)

  // Strategy list comes from the merged backend catalog (registry.ts), not a
  // hardcoded array — newly registered strategies appear after refreshCatalog().
  const availableStrategies = useMemo(
    () =>
      Object.values(strategies).map((s) => ({
        id: s.id,
        name: s.label,
        category: 'Momentum & Trend',
        desc: `${s.label} strategy (registry-loaded).`,
        tags: ['Zero Parity'],
      })),
    [catalogEpoch],
  )

  useEffect(() => {
    if (!isOpen) return
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  const activeStrategy =
    availableStrategies.find((s) => s.id === strategyId) || availableStrategies[0]
  const activeCount = Object.values(indicators).filter(Boolean).length

  return (
    <div className="relative flex items-center" ref={dropdownRef}>
      {/* Trigger Button */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setIsOpen((prev) => !prev)}
        className={`flex items-center gap-2 rounded-lg border px-2.5 py-1 text-xs font-semibold transition-all ${
          isOpen
            ? 'border-accent/80 bg-accent/15 text-accent shadow-sm'
            : 'border-line/60 bg-panel/70 text-ink hover:border-line hover:bg-panel2'
        }`}
        title="Indicators & Strategies (I)"
        aria-expanded={isOpen}
      >
        <span className="flex h-4 w-4 items-center justify-center rounded bg-accent/20 text-[10px] font-bold text-accent">
          ƒx
        </span>
        <span className="font-mono text-xs font-semibold">
          {strategyLabel(strategyId) || activeStrategy.name}
        </span>
        {activeCount > 1 && (
          <span className="rounded bg-accent/20 px-1 py-0.2 text-[9px] font-bold text-accent">
            +{activeCount - 1}
          </span>
        )}
        <ChevronDown className="h-3.5 w-3.5 text-muted" />
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div className="absolute left-0 top-full mt-1.5 z-40 w-80 rounded-xl border border-line/80 bg-[#0F172A] p-3 shadow-2xl ring-1 ring-white/10 animate-in fade-in zoom-in-95 duration-100">
          {/* Header */}
          <div className="mb-2.5 flex items-center justify-between border-b border-line/60 pb-2">
            <div className="flex items-center gap-1.5 text-xs font-bold text-ink">
              <Sparkles className="h-4 w-4 text-accent" />
              <span>INDICATORS & STRATEGIES</span>
            </div>
            {onOpenModal && (
              <button
                type="button"
                onClick={() => {
                  setIsOpen(false)
                  onOpenModal()
                }}
                className="flex items-center gap-1 rounded bg-panel2 px-1.5 py-0.5 text-[10px] font-medium text-accent hover:bg-accent/20 transition-colors"
                title="Open full indicator library modal"
              >
                <LayoutGrid className="h-3 w-3" />
                <span>Library</span>
              </button>
            )}
          </div>

          {/* Active Strategy Selector */}
          <div className="mb-3 space-y-1.5">
            <div className="text-[10px] font-bold uppercase tracking-wider text-muted">
              Select Active Strategy
            </div>
            <div className="space-y-1">
              {availableStrategies.map((s) => {
                const isSelected = strategyId === s.id
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => {
                      onSelectStrategy(s.id)
                    }}
                    className={`flex w-full flex-col gap-1 rounded-lg border p-2.5 text-left transition-all ${
                      isSelected
                        ? 'border-accent bg-accent/15 text-ink'
                        : 'border-line/40 bg-panel/40 text-muted hover:border-line hover:bg-panel hover:text-ink'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Activity className="h-3.5 w-3.5 text-accent" />
                        <span className="font-semibold text-xs text-ink">{s.name}</span>
                      </div>
                      {isSelected && <Check className="h-4 w-4 text-accent" />}
                    </div>
                    <p className="text-[11px] text-muted leading-tight">{s.desc}</p>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Quick Indicator Toggles */}
          <div className="space-y-1.5 pt-2 border-t border-line/60">
            <div className="text-[10px] font-bold uppercase tracking-wider text-muted">
              Overlay Indicator Visibility
            </div>
            <div className="space-y-1">
              {/* HalfTrend overlay */}
              <div className="flex items-center justify-between rounded-lg bg-panel/60 px-2.5 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="flex h-2 w-2 rounded-full bg-[#2962FF]" />
                  <span className="text-xs font-medium text-ink">HalfTrend Signals & Channels</span>
                </div>
                <button
                  type="button"
                  onClick={() => onToggleIndicator('strategy')}
                  className={`rounded p-1 transition-colors ${
                    indicators.strategy ? 'text-accent bg-accent/10' : 'text-muted/60 hover:text-muted'
                  }`}
                  title={indicators.strategy ? 'Hide Strategy Signals' : 'Show Strategy Signals'}
                >
                  {indicators.strategy ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                </button>
              </div>

              {/* VWAP */}
              <div className="flex items-center justify-between rounded-lg bg-panel/60 px-2.5 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="flex h-2 w-2 rounded-full bg-[#F59E0B]" />
                  <span className="text-xs font-medium text-ink">Session VWAP ± 2σ Bands</span>
                </div>
                <button
                  type="button"
                  onClick={() => onToggleIndicator('vwap')}
                  className={`rounded p-1 transition-colors ${
                    indicators.vwap ? 'text-accent bg-accent/10' : 'text-muted/60 hover:text-muted'
                  }`}
                  title={indicators.vwap ? 'Hide VWAP' : 'Show VWAP'}
                >
                  {indicators.vwap ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                </button>
              </div>

              {/* Volume Profile */}
              <div className="flex items-center justify-between rounded-lg bg-panel/60 px-2.5 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="flex h-2 w-2 rounded-full bg-[#94A3B8]" />
                  <span className="text-xs font-medium text-ink">Volume Profile (POC / VAH / VAL)</span>
                </div>
                <button
                  type="button"
                  onClick={() => onToggleIndicator('volumeProfile')}
                  className={`rounded p-1 transition-colors ${
                    indicators.volumeProfile ? 'text-accent bg-accent/10' : 'text-muted/60 hover:text-muted'
                  }`}
                  title={indicators.volumeProfile ? 'Hide Volume Profile' : 'Show Volume Profile'}
                >
                  {indicators.volumeProfile ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                </button>
              </div>

              {/* ADX Subplot */}
              <div className="flex items-center justify-between rounded-lg bg-panel/60 px-2.5 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="flex h-2 w-2 rounded-full bg-[#F59E0B]" />
                  <span className="text-xs font-medium text-ink">ADX (14) +DI / -DI Subplot</span>
                </div>
                <button
                  type="button"
                  onClick={() => onToggleIndicator('adx')}
                  className={`rounded p-1 transition-colors ${
                    indicators.adx ? 'text-accent bg-accent/10' : 'text-muted/60 hover:text-muted'
                  }`}
                  title={indicators.adx ? 'Hide ADX Subplot' : 'Show ADX Subplot'}
                >
                  {indicators.adx ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
          </div>

          {/* Full Library Modal Button */}
          {onOpenModal && (
            <div className="mt-2.5 pt-2 border-t border-line/40">
              <button
                type="button"
                onClick={() => {
                  setIsOpen(false)
                  onOpenModal()
                }}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-line/70 bg-panel2/80 py-1.5 text-xs font-semibold text-ink hover:border-accent/60 hover:text-accent transition-all"
              >
                <Layers className="h-3.5 w-3.5" />
                <span>Browse All Indicators (Press I)</span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
