import { AlertTriangle, Loader2, Inbox } from 'lucide-react'

export function LoadingState({ label = 'Loading candles…' }: { label?: string }) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-base/60 backdrop-blur-[1px]">
      <Loader2 className="h-7 w-7 animate-spin text-accent" />
      <span className="text-xs text-muted">{label}</span>
    </div>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-base/60">
      <Inbox className="h-7 w-7 text-muted/60" />
      <span className="text-sm font-medium text-muted">{title}</span>
      {hint && <span className="max-w-sm text-center text-xs text-muted/70">{hint}</span>}
    </div>
  )
}

/**
 * Corner chip shown when replay is armed but not yet playing. Deliberately
 * small so the ghosted historical context stays visible (unlike the old
 * full-screen overlay). Space also plays — hinted here.
 */
export function ReplayReadyState() {
  return (
    <div className="pointer-events-none absolute left-1/2 top-3 z-20 -translate-x-1/2 rounded-md border border-line/60 bg-panel/80 px-3 py-1 text-[11px] text-muted backdrop-blur-sm">
      Replay armed · <span className="font-mono text-accent">Space</span> to play
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-base/60">
      <AlertTriangle className="h-7 w-7 text-danger" />
      <span className="text-sm font-medium text-danger">Failed to load data</span>
      <span className="max-w-sm text-center font-mono text-xs text-muted">{message}</span>
      {onRetry ? (
        <button type="button" className="tbtn tbtn-primary mt-1" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  )
}
