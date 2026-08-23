import { useEffect, useState } from 'react'
import { TradeScreen } from './pages/TradeScreen'
import { useChartStore } from './store/chartStore'
import { TerminalRibbon } from './components/TerminalRibbon'
import { feedKind, FEED_META, type FeedKind } from './lib/feedStatus'
import { refreshCatalog } from './lib/registry'

export default function App() {
  const init = useChartStore((s) => s.init)
  const wsStatus = useChartStore((s) => s.wsStatus)
  const mode = useChartStore((s) => s.mode)
  const provider = useChartStore((s) => s.provider)

  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000))
  // Bump after catalog merge so strategy/indicator pickers re-read registry maps.
  const [catalogEpoch, setCatalogEpoch] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    void init()
    void refreshCatalog().then(() => setCatalogEpoch((n) => n + 1))
  }, [init])

  const feed: FeedKind | null = provider
    ? feedKind(mode, provider.provider, provider.live ?? false, wsStatus, wsStatus === 'stale')
    : null
  const m = feed ? FEED_META[feed] : null

  return (
    <div className="flex h-full flex-col bg-base text-ink">
      {provider && provider.provider !== 'dhan' && (
        <div className="border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-center text-[11px] font-mono text-amber-300">
          DEMO / {provider.provider === 'synthetic' ? 'SYNTHETIC' : 'OFFLINE'} DATA — live trading is unavailable
        </div>
      )}
      <TerminalRibbon now={now} catalogEpoch={catalogEpoch} />
      <main className="min-h-0 flex-1">
        <TradeScreen />
      </main>
      <footer className="flex items-center justify-between border-t border-line/60 bg-panel/80 px-3 py-1.5">
        <span className="text-[10px] font-mono text-muted/70">
          {provider
            ? provider.provider === 'dhan'
              ? 'nTrade futures terminal · live dhan feed'
              : `nTrade futures terminal · ${provider.provider} data`
            : 'nTrade futures terminal'}
        </span>
        {feed && m && (
          <span className={`text-[10px] font-mono font-medium ${m.text}`}>
            <span className={`inline-block h-1.5 w-1.5 rounded-full mr-1 align-middle ${m.dot}`} />
            {m.label}
          </span>
        )}
      </footer>
    </div>
  )
}
