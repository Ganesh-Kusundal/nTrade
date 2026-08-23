import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, X, Calendar, Layers, ShieldCheck, Check } from 'lucide-react'
import type { Contract, Root } from '../types/market'
import { fmtExpiry } from '../lib/format'
import { api } from '../api/client'

interface SymbolSearchModalProps {
  isOpen: boolean
  onClose: () => void
  roots: Root[]
  currentRoot: string | null
  currentContract: Contract | null
  onSelectContract: (root: string, contract: Contract) => void
}

type FilterCategory = 'ALL' | 'MCX' | 'NSE'

export function SymbolSearchModal({
  isOpen,
  onClose,
  roots,
  currentRoot,
  currentContract,
  onSelectContract,
}: SymbolSearchModalProps) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<FilterCategory>('ALL')
  const [selectedRootId, setSelectedRootId] = useState<string | null>(currentRoot ?? (roots[0]?.root ?? null))
  const [contracts, setContracts] = useState<Contract[]>([])
  const [loadingContracts, setLoadingContracts] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  // Auto focus input when opened
  useEffect(() => {
    if (isOpen) {
      setSelectedRootId(currentRoot ?? (roots[0]?.root ?? null))
      setQuery('')
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [isOpen, currentRoot, roots])

  // Handle escape key
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

  // Filter roots by category & search query
  const filteredRoots = useMemo(() => {
    const q = query.trim().toLowerCase()
    return roots.filter((r) => {
      // Category filter
      if (category === 'MCX' && !r.exchange.toUpperCase().includes('MCX')) return false
      if (category === 'NSE' && r.exchange.toUpperCase().includes('MCX')) return false

      // Query filter
      if (!q) return true
      const matchesRoot = r.root.toLowerCase().includes(q)
      const matchesDisplayName = r.display_name?.toLowerCase().includes(q)
      const matchesExchange = r.exchange.toLowerCase().includes(q)
      return matchesRoot || matchesDisplayName || matchesExchange
    })
  }, [roots, category, query])

  // Keep selected root synchronized with filter results
  useEffect(() => {
    if (filteredRoots.length > 0) {
      if (!selectedRootId || !filteredRoots.some((r) => r.root === selectedRootId)) {
        setSelectedRootId(filteredRoots[0].root)
      }
    } else {
      setSelectedRootId(null)
    }
  }, [filteredRoots, selectedRootId])

  // Fetch contracts when selected root changes
  useEffect(() => {
    if (!selectedRootId) {
      setContracts([])
      return
    }
    let cancelled = false
    setLoadingContracts(true)
    api
      .contracts(selectedRootId)
      .then((res) => {
        if (!cancelled) {
          setContracts(res.contracts ?? [])
          setLoadingContracts(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setContracts([])
          setLoadingContracts(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [selectedRootId])

  if (!isOpen) return null

  const selectedRootObj = roots.find((r) => r.root === selectedRootId)

  const handleSelect = (contract: Contract) => {
    if (selectedRootId) {
      onSelectContract(selectedRootId, contract)
      onClose()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-in fade-in duration-150">
      <div
        className="relative flex h-[540px] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-line/80 bg-[#0F172A] shadow-2xl ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header with Search Bar */}
        <div className="border-b border-line/60 bg-[#1E293B]/70 p-3">
          <div className="flex items-center gap-3">
            <Search className="h-5 w-5 text-muted" />
            <input
              ref={inputRef}
              type="text"
              className="flex-1 bg-transparent text-base font-medium text-ink placeholder-muted/60 outline-none"
              placeholder="Search symbol, commodity, index (e.g. CRUDEOIL, NIFTY, GOLD)..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                className="rounded p-1 text-muted hover:bg-panel2 hover:text-ink"
              >
                <X className="h-4 w-4" />
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-line/50 bg-panel2/60 px-2 py-1 text-xs font-mono text-muted hover:border-line hover:text-ink"
            >
              ESC
            </button>
          </div>

          {/* Category Tabs */}
          <div className="mt-3 flex items-center gap-2">
            {(['ALL', 'MCX', 'NSE'] as FilterCategory[]).map((cat) => (
              <button
                key={cat}
                type="button"
                className={`rounded-md px-3 py-1 text-xs font-semibold tracking-wide transition-all ${
                  category === cat
                    ? 'bg-accent text-base shadow-sm font-bold'
                    : 'bg-panel2/50 text-muted hover:bg-panel2 hover:text-ink'
                }`}
                onClick={() => setCategory(cat)}
              >
                {cat === 'ALL' ? 'All Instruments' : cat === 'MCX' ? 'MCX Commodities' : 'NSE Derivatives'}
              </button>
            ))}
            <span className="ml-auto text-[11px] font-mono text-muted/70">
              {filteredRoots.length} symbol{filteredRoots.length === 1 ? '' : 's'}
            </span>
          </div>
        </div>

        {/* 2-Column Body: Left = Roots, Right = Contracts/Expiries */}
        <div className="grid flex-1 grid-cols-12 min-h-0 overflow-hidden divide-x divide-line/60 bg-base/40">
          {/* Left column: Root Symbols */}
          <div className="col-span-5 flex flex-col overflow-y-auto p-2">
            <div className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-muted/80">
              Roots ({filteredRoots.length})
            </div>
            {filteredRoots.length === 0 ? (
              <div className="p-4 text-center text-xs text-muted">No symbols found matching "{query}"</div>
            ) : (
              <div className="space-y-1">
                {filteredRoots.map((r) => {
                  const isSelected = r.root === selectedRootId
                  const isCurrent = r.root === currentRoot
                  const isMcx = r.exchange.toUpperCase().includes('MCX')
                  return (
                    <button
                      key={r.root}
                      type="button"
                      onClick={() => setSelectedRootId(r.root)}
                      className={`flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left transition-all ${
                        isSelected
                          ? 'border border-accent/60 bg-accent/15 text-ink ring-1 ring-accent/30'
                          : 'border border-transparent bg-panel/40 text-muted hover:bg-panel hover:text-ink'
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm font-bold text-ink">{r.root}</span>
                          {isCurrent && (
                            <span className="rounded bg-accent/20 px-1.5 py-0.2 text-[9px] font-semibold text-accent">
                              ACTIVE
                            </span>
                          )}
                        </div>
                        <div className="truncate text-[11px] text-muted/80">
                          {r.display_name || r.root}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[9px] font-mono font-bold ${
                            isMcx
                              ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
                              : 'bg-blue-500/15 text-blue-300 border border-blue-500/30'
                          }`}
                        >
                          {isMcx ? 'MCX' : 'NSE'}
                        </span>
                        <span className="text-[10px] font-mono text-muted/60">
                          {r.n_contracts} exp
                        </span>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {/* Right column: Expiries / Contracts for the selected root */}
          <div className="col-span-7 flex flex-col overflow-y-auto p-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-accent" />
                <span className="text-xs font-bold text-ink">
                  {selectedRootObj?.root ?? 'Contracts'} Expiries
                </span>
                {selectedRootObj && (
                  <span className="text-[11px] text-muted">
                    ({selectedRootObj.exchange})
                  </span>
                )}
              </div>
              <span className="text-[10px] font-mono text-muted/70">
                Click expiry to load chart
              </span>
            </div>

            {loadingContracts ? (
              <div className="flex flex-1 items-center justify-center p-8 text-xs text-muted">
                <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-accent border-r-transparent mr-2" />
                Loading contracts...
              </div>
            ) : contracts.length === 0 ? (
              <div className="flex flex-1 items-center justify-center p-8 text-xs text-muted">
                No active contracts for {selectedRootId}
              </div>
            ) : (
              <div className="space-y-2">
                {contracts.map((c) => {
                  const isCurrent = c.symbol === currentContract?.symbol
                  return (
                    <button
                      key={c.contract_id || c.symbol}
                      type="button"
                      disabled={c.is_expired}
                      onClick={() => handleSelect(c)}
                      className={`group flex w-full items-center justify-between rounded-lg border p-3 text-left transition-all ${
                        isCurrent
                          ? 'border-accent bg-accent/20 text-ink shadow-sm'
                          : c.is_expired
                          ? 'border-line/30 bg-panel2/20 text-muted/40 cursor-not-allowed opacity-50'
                          : 'border-line/60 bg-panel/70 text-ink hover:border-accent/60 hover:bg-panel2/90'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className={`flex h-8 w-8 items-center justify-center rounded-lg ${
                            isCurrent
                              ? 'bg-accent text-base'
                              : 'bg-panel2 text-muted group-hover:text-accent'
                          }`}
                        >
                          <Calendar className="h-4 w-4" />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-semibold text-ink">
                              {c.symbol}
                            </span>
                            {c.is_front_month && !c.is_expired && (
                              <span className="rounded bg-accent/20 px-1.5 py-0.5 text-[9px] font-bold text-accent border border-accent/40">
                                Front Month
                              </span>
                            )}
                            {c.is_expired && (
                              <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-[9px] font-medium text-red-300">
                                Expired
                              </span>
                            )}
                          </div>
                          <div className="mt-0.5 flex items-center gap-3 text-[11px] font-mono text-muted">
                            <span>Expiry: <b className="text-ink/80">{fmtExpiry(c.expiry)}</b></span>
                            <span>·</span>
                            <span>Lot: <b className="text-ink/80">{c.lot_size}</b></span>
                            <span>·</span>
                            <span>Tick: <b className="text-ink/80">{c.tick_size}</b></span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {isCurrent && <Check className="h-5 w-5 text-accent" />}
                        <span className="rounded border border-line/60 bg-panel2/60 px-2 py-1 text-[11px] font-mono font-medium text-muted group-hover:border-accent/50 group-hover:text-accent">
                          Select ↵
                        </span>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-line/60 bg-[#1E293B]/70 px-4 py-2 text-[11px] text-muted">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-3.5 w-3.5 text-accent" />
            <span>Zero-parity market instrument resolution</span>
          </div>
          <div className="flex items-center gap-3 font-mono text-[10px]">
            <span><kbd className="rounded bg-panel2 px-1.5 py-0.5 border border-line">↑↓</kbd> Navigate</span>
            <span><kbd className="rounded bg-panel2 px-1.5 py-0.5 border border-line">↵</kbd> Select</span>
            <span><kbd className="rounded bg-panel2 px-1.5 py-0.5 border border-line">Esc</kbd> Close</span>
          </div>
        </div>
      </div>
    </div>
  )
}
