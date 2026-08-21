import { useEffect } from 'react'
import { useChartStore } from '../store/chartStore'
import { DEFAULT_EXCHANGE } from '../lib/constants'
import { fmtISTClock } from '../lib/istTime'
import { feedKind, FEED_META, isSessionOpen, type FeedKind } from '../lib/feedStatus'

const INT_MAP = [
  { key: '1', interval: '1m' as const },
  { key: '2', interval: '5m' as const },
  { key: '3', interval: '15m' as const },
  { key: '4', interval: '1h' as const },
  { key: '5', interval: '1D' as const },
  { key: '0', interval: 'Range' as const },
]

export function TerminalRibbon({ now }: { now: number }) {
  const interval = useChartStore((s) => s.interval)
  const setInterval = useChartStore((s) => s.selectInterval)
  const mode = useChartStore((s) => s.mode)
  const setMode = useChartStore((s) => s.setMode)
  const wsStatus = useChartStore((s) => s.wsStatus)
  const contract = useChartStore((s) => s.contract)
  const root = useChartStore((s) => s.root)
  const provider = useChartStore((s) => s.provider)

  const exchange = contract?.exchange ?? (root ? (useChartStore.getState().roots.find((r) => r.root === root)?.exchange) : DEFAULT_EXCHANGE)
  const open = isSessionOpen(exchange, root, now)
  const feed: FeedKind | null = provider
    ? feedKind(mode, provider.provider, provider.live ?? false, wsStatus, wsStatus === 'stale')
    : null
  const meta = feed ? FEED_META[feed] : null

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const k = e.key
      if (k >= '1' && k <= '5') {
        setInterval(
          k === '1' ? '1m' : k === '2' ? '5m' : k === '3' ? '15m' : k === '4' ? '1h' : '1D',
        )
        e.preventDefault()
      } else if (k === '0') {
        setInterval('Range')
        e.preventDefault()
      } else if (k === 'l' || k === 'L') {
        setMode('live')
        e.preventDefault()
      } else if (k === 'p' || k === 'P') {
        setMode('replay')
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [setInterval, setMode])

  return (
    <header className="flex items-center gap-3 border-b border-line/60 bg-panel/80 px-3 py-2">
      {/* brand */}
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded bg-accent/15 font-mono text-xs font-bold text-accent">
          nT
        </span>
        <span className="text-sm font-semibold tracking-tight">nTrade</span>
        <span className="rounded border border-line/60 bg-panel2/60 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted">
          Futures Terminal
        </span>
      </div>

      {/* function key strip — interval */}
      <div className="flex items-center gap-1 ml-2">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted mr-1">INT</span>
        {INT_MAP.map(({ key, interval: iv }) => {
          const isActive = interval === iv
          return (
            <button
              key={key}
              type="button"
              className={`rounded-md border text-[11px] font-mono leading-none transition-colors duration-150 ${
                isActive
                  ? 'border-accent/80 bg-accent/15 text-accent ring-1 ring-accent/40'
                  : 'border-line/60 bg-panel2/60 text-muted hover:border-line hover:text-ink'
              }`}
              onClick={() => setInterval(iv)}
              aria-label={`Interval ${iv}`}
              aria-pressed={isActive}
              title={`F${key} — ${iv}`}
            >
              <span className="block font-semibold">F{key}</span>
              <span className="block text-[9px] leading-tight opacity-70">{iv}</span>
            </button>
          )
        })}
      </div>

      {/* mode key */}
      <div className="flex items-center gap-1">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted mr-1">MODE</span>
        <button
          type="button"
          className={`rounded-md border px-2.5 py-1 text-xs font-mono font-semibold transition-colors duration-150 ${
            mode === 'live'
              ? 'border-accent/70 bg-accent/10 text-accent'
              : 'border-line/60 bg-panel2/60 text-muted hover:border-line hover:text-ink'
          }`}
          onClick={() => setMode(mode === 'live' ? 'replay' : 'live')}
          aria-pressed={mode === 'replay'}
          title="Live or replay mode"
        >
          {mode === 'live' ? 'LIVE' : 'REPLAY'}
        </button>
      </div>

      {/* right status: session · feed · clock */}
      <div className="ml-auto flex items-center gap-3">
        <span className="flex items-center gap-1.5 rounded-md border border-line/60 bg-panel2/60 px-2 py-1">
          <span
            className={`h-1.5 w-1.5 rounded-full ${open ? 'bg-accent' : 'bg-slate-500'}`}
            aria-hidden
          />
          <span className={`text-[11px] font-mono font-medium ${open ? 'text-accent' : 'text-muted'}`}>
            {open ? 'OPEN' : 'CLOSED'}
          </span>
        </span>
        <span className="flex items-center gap-1.5 rounded-md border border-line/60 bg-panel2/60 px-2 py-1" title="Live feed status">
          <span
            className={`h-1.5 w-1.5 rounded-full ${meta?.dot ?? 'bg-slate-500'}`}
            aria-hidden
          />
          <span className={`text-[11px] font-mono font-medium ${meta?.text ?? 'text-muted'}`}>{meta?.label ?? '—'}</span>
        </span>
        <span className="font-mono text-[11px] tabular-nums text-ink" aria-label="IST time">
          {fmtISTClock(now)}
        </span>
      </div>
    </header>
  )
}
