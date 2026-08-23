import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Clock, Layers } from 'lucide-react'
import type { Interval } from '../types/market'

interface TimeframeSelectorProps {
  value: Interval
  onChange: (interval: Interval) => void
  disabled?: boolean
}

interface GroupedInterval {
  group: string
  items: { interval: Interval; label: string; desc: string }[]
}

const FAVORITES: Interval[] = ['1m', '5m', '15m', '1h', '1D', 'Range']

const INTERVAL_GROUPS: GroupedInterval[] = [
  {
    group: 'Minutes',
    items: [
      { interval: '1m', label: '1 min', desc: '1 Minute standard bars' },
      { interval: '5m', label: '5 min', desc: '5 Minutes scalp bars' },
      { interval: '15m', label: '15 min', desc: '15 Minutes intraday trend' },
    ],
  },
  {
    group: 'Hours & Days',
    items: [
      { interval: '1h', label: '1 hour', desc: '60 Minutes structural view' },
      { interval: '1D', label: '1 day', desc: 'Daily session bars' },
    ],
  },
  {
    group: 'Price Action & Volatility',
    items: [
      { interval: 'Range', label: 'Range (Auto ATR)', desc: 'Volatility-sized non-time price bars' },
    ],
  },
]

export function TimeframeSelector({ value, onChange, disabled }: TimeframeSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement | null>(null)

  // Click outside to close
  useEffect(() => {
    if (!isOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  const select = (iv: Interval) => {
    onChange(iv)
    setIsOpen(false)
  }

  return (
    <div className="relative flex items-center gap-1" ref={dropdownRef}>
      {/* Quick Favorite Buttons */}
      <div className="flex items-center gap-0.5 rounded-lg border border-line/60 bg-panel/70 p-0.5" role="group" aria-label="Timeframes">
        {FAVORITES.map((iv) => {
          const isActive = value === iv
          return (
            <button
              key={iv}
              type="button"
              disabled={disabled}
              onClick={() => onChange(iv)}
              className={`rounded px-2 py-1 text-xs font-semibold font-mono transition-all ${
                isActive
                  ? 'bg-accent text-base shadow-sm font-bold'
                  : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
              title={iv === 'Range' ? 'Range bars (price action)' : `${iv} timeframe`}
            >
              {iv}
            </button>
          )
        })}

        {/* Dropdown Toggle */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => setIsOpen((prev) => !prev)}
          className={`flex items-center justify-center rounded px-1.5 py-1 text-muted transition-colors hover:bg-panel2 hover:text-ink ${
            isOpen ? 'bg-panel2 text-ink' : ''
          }`}
          title="All timeframes & intervals"
          aria-expanded={isOpen}
        >
          <ChevronDown className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Timeframe Dropdown Menu */}
      {isOpen && (
        <div className="absolute left-0 top-full mt-1.5 z-40 w-64 rounded-xl border border-line/80 bg-[#0F172A] p-2 shadow-2xl ring-1 ring-white/10 animate-in fade-in zoom-in-95 duration-100">
          <div className="mb-2 flex items-center justify-between border-b border-line/60 px-2 py-1 text-[11px] font-bold text-muted">
            <span className="flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5 text-accent" />
              TIMEFRAME SELECTOR
            </span>
          </div>

          <div className="space-y-2">
            {INTERVAL_GROUPS.map((grp) => (
              <div key={grp.group} className="space-y-1">
                <div className="px-2 text-[10px] font-bold uppercase tracking-wider text-muted/70">
                  {grp.group}
                </div>
                <div className="space-y-0.5">
                  {grp.items.map(({ interval: iv, label, desc }) => {
                    const isSelected = value === iv
                    return (
                      <button
                        key={iv}
                        type="button"
                        onClick={() => select(iv)}
                        className={`flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors ${
                          isSelected
                            ? 'bg-accent/20 text-accent font-bold border border-accent/40'
                            : 'text-ink hover:bg-panel2'
                        }`}
                      >
                        <div className="flex flex-col">
                          <span className="font-mono font-semibold">{label}</span>
                          <span className="text-[10px] text-muted">{desc}</span>
                        </div>
                        {iv === 'Range' ? (
                          <Layers className="h-3.5 w-3.5 text-accent opacity-80" />
                        ) : (
                          <span className="font-mono text-[10px] text-muted">{iv}</span>
                        )}
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
