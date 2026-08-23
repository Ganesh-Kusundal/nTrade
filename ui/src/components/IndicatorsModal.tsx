import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Search,
  X,
  Sparkles,
  Activity,
  BarChart2,
  Layers,
  Check,
  Plus,
  ShieldCheck,
} from 'lucide-react'
import type { IndicatorToggles } from './ChartPanel'
import { indicators as indicatorRegistry, strategies, strategyLabel } from '../lib/registry'

interface IndicatorsModalProps {
  isOpen: boolean
  onClose: () => void
  strategyId: string
  onSelectStrategy: (id: string) => void
  indicators: IndicatorToggles
  onToggleIndicator: (key: keyof IndicatorToggles) => void
}

type CategoryTab = 'ALL' | 'TREND' | 'VOLUME' | 'ACTIVE'

interface IndicatorItem {
  key: keyof IndicatorToggles
  name: string
  shortLabel: string
  category: 'TREND' | 'VOLUME'
  categoryLabel: string
  desc: string
  color: string
  tags: string[]
}

// Toggle keys are the chart-overlay contract (ChartPanel renders these under
// fixed keys). Display labels are sourced from the merged backend registry so
// names stay in sync; new backend indicators appear once the catalog is merged.
const _ht_label = strategies['halftrend']?.label ?? 'HalfTrend'
const _vwap_label = indicatorRegistry['vwap']?.label ?? 'VWAP (per session)'
const _vp_label = indicatorRegistry['volume_profile']?.label ?? 'Volume Profile (FRVP)'

const INDICATOR_ITEMS: IndicatorItem[] = [
  {
    key: 'strategy',
    name: `${_ht_label} Trend Following & Signals`,
    shortLabel: _ht_label,
    category: 'TREND',
    categoryLabel: 'Trend & Momentum',
    desc: 'ATR(100) / Deviation 2.0 dynamic trend channel with real-time arrow signals on trend flips',
    color: '#2962FF',
    tags: ['Strategy', 'ATR Dynamic', 'Trend Reversal', 'Zero Parity'],
  },
  {
    key: 'vwap',
    name: `${_vwap_label} (VWAP ± 2σ)`,
    shortLabel: _vwap_label,
    category: 'VOLUME',
    categoryLabel: 'Volume & Price',
    desc: 'Per-session benchmark average price with ±2.0 Standard Deviation bands',
    color: '#F59E0B',
    tags: ['VWAP', 'Standard Deviation', 'Session Anchored'],
  },
  {
    key: 'volumeProfile',
    name: `${_vp_label} POC / VAH / VAL`,
    shortLabel: _vp_label,
    category: 'VOLUME',
    categoryLabel: 'Order Flow & Profile',
    desc: 'Session volume distribution with Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL)',
    color: '#94A3B8',
    tags: ['Profile', 'POC', 'VAH/VAL', 'Order Flow'],
  },
  {
    key: 'adx',
    name: 'Average Directional Index (ADX 14) Subplot',
    shortLabel: 'ADX Subplot',
    category: 'TREND',
    categoryLabel: 'Trend & Momentum',
    desc: 'ADX (14) trend strength gauge with +DI / -DI directional lines and 20/25 baseline thresholds in a bottom subplot',
    color: '#F59E0B',
    tags: ['ADX 14', '+DI / -DI', 'Trend Strength', 'Subplot'],
  },
]

export function IndicatorsModal({
  isOpen,
  onClose,
  strategyId,
  indicators,
  onToggleIndicator,
}: IndicatorsModalProps) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<CategoryTab>('ALL')
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (isOpen) {
      setQuery('')
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  const filteredItems = useMemo(() => {
    const q = query.trim().toLowerCase()
    return INDICATOR_ITEMS.filter((item) => {
      const isActive = indicators[item.key]
      if (category === 'ACTIVE' && !isActive) return false
      if (category === 'TREND' && item.category !== 'TREND') return false
      if (category === 'VOLUME' && item.category !== 'VOLUME') return false

      if (!q) return true
      const matchName = item.name.toLowerCase().includes(q)
      const matchDesc = item.desc.toLowerCase().includes(q)
      const matchTags = item.tags.some((t) => t.toLowerCase().includes(q))
      return matchName || matchDesc || matchTags
    })
  }, [query, category, indicators])

  if (!isOpen) return null

  const activeCount = Object.values(indicators).filter(Boolean).length

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-in fade-in duration-150">
      <div
        className="relative flex h-[540px] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-line/80 bg-[#0F172A] shadow-2xl ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="border-b border-line/60 bg-[#1E293B]/70 p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded bg-accent/20 text-xs font-bold text-accent">
                ƒx
              </span>
              <h2 className="text-sm font-bold text-ink tracking-tight">
                Indicators, Metrics & Strategies
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded p-1 text-muted hover:bg-panel2 hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Search Bar */}
          <div className="flex items-center gap-2 rounded-lg border border-line/60 bg-[#0F172A] px-3 py-2">
            <Search className="h-4 w-4 text-muted" />
            <input
              ref={inputRef}
              type="text"
              className="flex-1 bg-transparent text-xs font-medium text-ink placeholder-muted/60 outline-none"
              placeholder="Search indicators, strategies, volume profile (e.g. HalfTrend, VWAP, Volume Profile)..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                className="text-muted hover:text-ink"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* 2-Column Body: Left = Category Tabs, Right = Indicator List */}
        <div className="grid flex-1 grid-cols-12 min-h-0 overflow-hidden divide-x divide-line/60 bg-base/40">
          {/* Left Category Sidebar */}
          <div className="col-span-4 flex flex-col gap-1 overflow-y-auto p-2 bg-panel/30">
            <button
              type="button"
              onClick={() => setCategory('ALL')}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs font-semibold transition-all ${
                category === 'ALL'
                  ? 'bg-accent/15 text-accent border border-accent/40 font-bold'
                  : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
            >
              <div className="flex items-center gap-2">
                <Sparkles className="h-3.5 w-3.5" />
                <span>All Indicators</span>
              </div>
              <span className="font-mono text-[10px] opacity-70">{INDICATOR_ITEMS.length}</span>
            </button>

            <button
              type="button"
              onClick={() => setCategory('ACTIVE')}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs font-semibold transition-all ${
                category === 'ACTIVE'
                  ? 'bg-accent/15 text-accent border border-accent/40 font-bold'
                  : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
            >
              <div className="flex items-center gap-2">
                <Check className="h-3.5 w-3.5 text-accent" />
                <span>Active on Chart</span>
              </div>
              <span className="rounded bg-accent/20 px-1.5 py-0.2 font-mono text-[10px] font-bold text-accent">
                {activeCount}
              </span>
            </button>

            <div className="my-1 border-t border-line/40" />

            <button
              type="button"
              onClick={() => setCategory('TREND')}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs font-semibold transition-all ${
                category === 'TREND'
                  ? 'bg-accent/15 text-accent border border-accent/40 font-bold'
                  : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
            >
              <div className="flex items-center gap-2">
                <Activity className="h-3.5 w-3.5" />
                <span>Trend & Momentum</span>
              </div>
            </button>

            <button
              type="button"
              onClick={() => setCategory('VOLUME')}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs font-semibold transition-all ${
                category === 'VOLUME'
                  ? 'bg-accent/15 text-accent border border-accent/40 font-bold'
                  : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
            >
              <div className="flex items-center gap-2">
                <BarChart2 className="h-3.5 w-3.5" />
                <span>Volume & Profile</span>
              </div>
            </button>
          </div>

          {/* Right Indicator List */}
          <div className="col-span-8 flex flex-col overflow-y-auto p-3 space-y-2.5">
            {filteredItems.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center p-8 text-center text-xs text-muted">
                <Layers className="h-8 w-8 text-muted/40 mb-2" />
                <span>No indicators found matching "{query}"</span>
              </div>
            ) : (
              filteredItems.map((item) => {
                const isActive = indicators[item.key]
                return (
                  <div
                    key={item.key}
                    className={`group flex items-start justify-between rounded-lg border p-3 transition-all ${
                      isActive
                        ? 'border-accent/60 bg-accent/[0.08] shadow-sm'
                        : 'border-line/60 bg-panel/60 hover:border-line hover:bg-panel2/80'
                    }`}
                  >
                    <div className="flex items-start gap-3 min-w-0 flex-1 pr-3">
                      <span
                        className="mt-1 h-3 w-3 rounded-full flex-shrink-0"
                        style={{ backgroundColor: item.color }}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold text-xs text-ink truncate">{item.name}</h3>
                          {isActive && (
                            <span className="rounded bg-accent/20 px-1.5 py-0.2 text-[9px] font-bold text-accent border border-accent/30">
                              ACTIVE
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-[11px] text-muted leading-relaxed">
                          {item.desc}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-1">
                          <span className="rounded bg-panel2 px-1.5 py-0.2 text-[9px] font-mono text-muted/80">
                            {item.categoryLabel}
                          </span>
                          {item.tags.map((t) => (
                            <span
                              key={t}
                              className="rounded bg-panel2/80 px-1.5 py-0.2 text-[9px] font-mono text-muted/60"
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>

                    {/* Toggle Button */}
                    <button
                      type="button"
                      onClick={() => onToggleIndicator(item.key)}
                      className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold transition-all ${
                        isActive
                          ? 'bg-accent text-base font-bold shadow-sm hover:bg-accent/90'
                          : 'border border-line/70 bg-panel2/70 text-ink hover:border-accent/60 hover:text-accent'
                      }`}
                    >
                      {isActive ? (
                        <>
                          <Check className="h-3.5 w-3.5" />
                          <span>Added</span>
                        </>
                      ) : (
                        <>
                          <Plus className="h-3.5 w-3.5" />
                          <span>Add to Chart</span>
                        </>
                      )}
                    </button>
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-line/60 bg-[#1E293B]/70 px-4 py-2 text-[11px] text-muted">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-3.5 w-3.5 text-accent" />
            <span>Active strategy: <b className="text-ink">{strategyLabel(strategyId)}</b></span>
          </div>
          <div className="flex items-center gap-3 font-mono text-[10px]">
            <span>{activeCount} active indicator{activeCount === 1 ? '' : 's'}</span>
            <span>·</span>
            <span><kbd className="rounded bg-panel2 px-1.5 py-0.5 border border-line">Esc</kbd> Close</span>
          </div>
        </div>
      </div>
    </div>
  )
}
