import { Radio, Rewind } from 'lucide-react'
import type { Mode } from '../types/market'

interface ModeToggleProps {
  mode: Mode
  onChange: (mode: Mode) => void
  disabled?: boolean
}

export function ModeToggle({ mode, onChange, disabled }: ModeToggleProps) {
  return (
    <div
      className="inline-flex items-center rounded-md border border-line/60 bg-panel2/40 p-0.5"
      role="group"
      aria-label="Chart mode"
    >
      <button
        type="button"
        className={`inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors duration-150
          ${mode === 'live' ? 'bg-accent/15 text-accent' : 'text-muted hover:text-ink'}
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60`}
        disabled={disabled}
        onClick={() => onChange('live')}
        aria-pressed={mode === 'live'}
      >
        <Radio className="h-3.5 w-3.5" />
        Live
      </button>
      <button
        type="button"
        className={`inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors duration-150
          ${mode === 'replay' ? 'bg-accent/15 text-accent' : 'text-muted hover:text-ink'}
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60`}
        disabled={disabled}
        onClick={() => onChange('replay')}
        aria-pressed={mode === 'replay'}
      >
        <Rewind className="h-3.5 w-3.5" />
        Replay
      </button>
    </div>
  )
}
