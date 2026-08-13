import { useEffect, useState } from 'react'
import { TradeScreen } from './pages/TradeScreen'
import { useChartStore } from './store/chartStore'
import { isMcxSession } from './lib/marketHours'
import { feedKind, FEED_META, isSessionOpen, type FeedKind } from './lib/feedStatus'
import { fmtISTClock } from './lib/istTime'

export default function App() {
  const init = useChartStore((s) => s.init)
  const exchange = useChartStore((s) => s.contract?.exchange)
  const root = useChartStore((s) => s.root)
  const mode = useChartStore((s) => s.mode)
  const live = useChartStore((s) => s.provider?.live ?? false)
  const wsStatus = useChartStore((s) => s.wsStatus)

  // Live IST clock for the chrome — ticks once per second, independent of data.
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000))
  useEffect(() => {
    const id = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    void init()
  }, [init])

  const feed: FeedKind = feedKind(mode, live, wsStatus)
  const m = FEED_META[feed]
  const open = isSessionOpen(exchange, root, now)
  const sessionLabel = isMcxSession(exchange, root) ? 'MCX' : 'NSE · NFO'

  return (
    <div className="flex h-full flex-col bg-base text-ink">
      <header className="flex items-center justify-between border-b border-line/60 bg-panel/50 px-4 py-2">
        <div className="flex items-center gap-2.5">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent/15 font-mono text-xs font-bold text-accent">
            n
          </span>
          <span className="text-sm font-semibold tracking-tight">nTrade</span>
          <span className="rounded border border-line/60 bg-panel2/60 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted">
            Futures Terminal
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] text-muted">{sessionLabel}</span>
          {/* ponytail: weekday-only session gate; no holiday calendar in repo */}
          <span
            className={`inline-flex items-center gap-1.5 rounded-md border border-line/60 bg-panel2/60 px-2 py-1 font-mono text-[11px] font-medium ${
              open ? 'text-accent' : 'text-muted'
            }`}
            title="Exchange session hours (IST); closed on weekends"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${open ? 'bg-accent' : 'bg-slate-400'}`} />
            {open ? 'OPEN' : 'CLOSED'}
          </span>
          <span className="font-mono text-[11px] tabular-nums text-ink">{fmtISTClock(now)}</span>
          <span
            className={`inline-flex items-center gap-1.5 rounded-md border border-line/60 bg-panel2/60 px-2 py-1 text-[11px] font-medium ${m.text}`}
            title="Live feed status (replay mode uses historical data only)"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
            {m.label}
          </span>
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <TradeScreen />
      </main>
    </div>
  )
}
