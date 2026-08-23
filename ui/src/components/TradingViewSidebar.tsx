import { useState } from 'react'
import {
  Crosshair,
  TrendingUp,
  Ruler,
  Maximize,
  Layers,
} from 'lucide-react'

export function TradingViewSidebar() {
  const [activeTool, setActiveTool] = useState<'crosshair' | 'line' | 'ruler' | 'profile'>('crosshair')

  const tools = [
    { id: 'crosshair', icon: Crosshair, label: 'Crosshair Mode' },
    { id: 'line', icon: TrendingUp, label: 'Trend Lines & Projections' },
    { id: 'ruler', icon: Ruler, label: 'Measure Range & Ticks' },
    { id: 'profile', icon: Layers, label: 'Volume Profile Ranges' },
  ] as const

  return (
    <aside className="flex w-10 flex-col items-center justify-between border-r border-line/60 bg-[#0F172A] py-2">
      {/* Top Tool Icons */}
      <div className="flex flex-col items-center gap-1.5">
        {tools.map((t) => {
          const Icon = t.icon
          const isActive = activeTool === t.id
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setActiveTool(t.id)}
              className={`flex h-7 w-7 items-center justify-center rounded-md transition-all ${
                isActive
                  ? 'bg-accent/20 text-accent font-bold ring-1 ring-accent/40'
                  : 'text-muted/80 hover:bg-panel2 hover:text-ink'
              }`}
              title={t.label}
            >
              <Icon className="h-4 w-4" />
            </button>
          )
        })}
      </div>

      {/* Bottom Tool Icons */}
      <div className="flex flex-col items-center gap-1.5">
        <button
          type="button"
          onClick={() => {
            const el = document.documentElement
            if (!document.fullscreenElement) {
              el.requestFullscreen?.().catch(() => {})
            } else {
              document.exitFullscreen?.().catch(() => {})
            }
          }}
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-panel2 hover:text-ink"
          title="Toggle Fullscreen"
        >
          <Maximize className="h-4 w-4" />
        </button>
      </div>
    </aside>
  )
}
