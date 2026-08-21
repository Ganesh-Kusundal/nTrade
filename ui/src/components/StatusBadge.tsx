import { feedKind, FEED_META, type WsStatus } from '../lib/feedStatus'
import type { Mode } from '../types/market'

interface StatusBadgeProps {
  source: string | null
  provider: string | null | undefined
  live: boolean
  wsStatus: WsStatus
  mode: Mode
}

/**
 * One feed chip for the header — mirrors the chrome's truth. The green dot is
 * reserved for `streaming` only; historical / offline / replay are muted so a
 * disconnected session can never read as "live".
 */
export function StatusBadge({ source, provider, live, wsStatus, mode }: StatusBadgeProps) {
  const feed = feedKind(mode, provider, live, wsStatus, wsStatus === 'stale')
  const m = FEED_META[feed]
  const streaming = feed === 'streaming'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border border-line/60 bg-panel2/60 px-2 py-1 text-[11px] font-medium ${m.text}`}
      title={streaming ? 'Live broker data' : 'Historical data (no live stream)'}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
      {m.label}
      {source ? <span className="text-muted/70"> · {source}</span> : null}
    </span>
  )
}
