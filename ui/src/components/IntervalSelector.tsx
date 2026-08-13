import type { Interval } from '../types/market'

interface IntervalSelectorProps {
  value: Interval
  onChange: (interval: Interval) => void
  disabled?: boolean
}

const TIMEFRAMES: Interval[] = ['1m', '5m', '15m', '1h', '1D']
const CHART_TYPES: Interval[] = ['Range']

export function IntervalSelector({ value, onChange, disabled }: IntervalSelectorProps) {
  const pill = (iv: Interval) => (
    <button
      key={iv}
      type="button"
      className={`tpill ${value === iv ? 'tpill-active' : ''}`}
      disabled={disabled}
      onClick={() => onChange(iv)}
      aria-pressed={value === iv}
      title={iv === 'Range' ? 'Range bars (price-based, auto ATR size)' : undefined}
    >
      {iv}
    </button>
  )
  return (
    <div className="flex items-center gap-1.5" role="group" aria-label="Interval">
      {TIMEFRAMES.map(pill)}
      <span className="mx-0.5 h-4 w-px bg-line/60" aria-hidden="true" />
      {CHART_TYPES.map(pill)}
    </div>
  )
}
