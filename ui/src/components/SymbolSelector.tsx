import type { Root } from '../types/market'

interface SymbolSelectorProps {
  roots: Root[]
  selected: string | null
  disabled?: boolean
  onSelect: (root: string) => void
}

export function SymbolSelector({ roots, selected, disabled, onSelect }: SymbolSelectorProps) {
  if (roots.length === 0) {
    return <div className="text-xs text-muted">No futures roots available.</div>
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Root symbol">
      {roots.map((r) => (
        <button
          key={r.root}
          type="button"
          className={`tpill ${selected === r.root ? 'tpill-active' : ''}`}
          disabled={disabled}
          onClick={() => onSelect(r.root)}
          aria-pressed={selected === r.root}
        >
          {r.root}
          <span className="ml-1 text-[10px] text-muted">{r.n_contracts}</span>
        </button>
      ))}
    </div>
  )
}
