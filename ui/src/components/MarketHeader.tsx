import type { InProgressBar } from '../hooks/replayVisible'
import type { Contract, Quote } from '../types/market'
import { StatusBadge } from './StatusBadge'

interface MarketHeaderProps {
  contract: Contract | null
  quote: Quote | null
  lastClose: number | null
  source: string | null
  live: boolean
  wsStatus: 'connected' | 'disconnected' | 'reconnecting' | 'off'
  mode: 'live' | 'replay'
  /** In-progress bar during tick replay — current tick price vs real bar OHLC. */
  replayBar?: InProgressBar | null
}

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

function fmtExpiry(iso: string | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-').map(Number)
  return `${d} ${MONTHS[m - 1]} ${String(y).slice(2)}`
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-[52px] flex-col">
      <span className="treadout-label">{label}</span>
      <span className="treadout-value">{value}</span>
    </div>
  )
}

export function MarketHeader({ contract, quote, lastClose, source, live, wsStatus, mode, replayBar }: MarketHeaderProps) {
  const price = lastClose ?? quote?.ltp ?? null
  const change = quote?.change ?? (price && quote ? price - quote.prev_close : 0)
  const changePct = quote?.change_pct ?? 0
  const up = (change ?? 0) >= 0

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <h1 className="text-lg font-semibold tracking-tight text-ink">
            {contract?.root ?? '—'}
            <span className="ml-2 font-mono text-sm font-normal text-muted">{contract?.symbol ?? ''}</span>
          </h1>
          <span className="rounded border border-line/60 bg-panel2/60 px-1.5 py-0.5 font-mono text-[11px] text-muted">
            {fmtExpiry(contract?.expiry)}
          </span>
        </div>
        {contract && (
          <div className="font-mono text-[11px] text-muted">
            Lot {contract.lot_size} · Tick {contract.tick_size} · {contract.exchange}
          </div>
        )}
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-baseline gap-2">
          <span className={`font-mono text-2xl font-semibold ${price == null ? 'text-muted' : up ? 'text-accent' : 'text-danger'}`}>
            {price == null ? '—' : price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          </span>
          {price != null && change != null && (
            <span className={`font-mono text-sm ${up ? 'text-accent' : 'text-danger'}`}>
              {up ? '+' : ''}
              {change.toFixed(2)} ({up ? '+' : ''}
              {changePct.toFixed(2)}%)
            </span>
          )}
        </div>

        <div className="hidden items-center gap-4 border-l border-line/60 pl-4 sm:flex">
          <Readout label="Open" value={quote?.open != null ? quote.open.toFixed(2) : '—'} />
          <Readout label="High" value={quote?.high != null ? quote.high.toFixed(2) : '—'} />
          <Readout label="Low" value={quote?.low != null ? quote.low.toFixed(2) : '—'} />
          <Readout label="Prev" value={quote?.prev_close != null ? quote.prev_close.toFixed(2) : '—'} />
          <Readout label="Vol" value={quote?.volume != null ? quote.volume.toLocaleString('en-IN') : '—'} />
        </div>

        {replayBar && (
          <div
            className="hidden flex-col gap-1 rounded-md border border-line/60 bg-panel2/60 px-2.5 py-1 font-mono sm:flex"
            title="Intra-bar replay: current synthesized tick price vs the bar's real OHLCV"
          >
            <span className="flex items-baseline gap-2 text-[11px]">
              <span className="text-muted">tick</span>
              <span className="text-sm font-semibold text-ink">
                {replayBar.tickPrice != null ? replayBar.tickPrice.toFixed(2) : '—'}
              </span>
              <span className="text-muted/70">
                {replayBar.revealed}/{replayBar.steps}
              </span>
            </span>
            <span className="text-[10px] text-muted/80">
              O {replayBar.bar.open.toFixed(2)} H {replayBar.bar.high.toFixed(2)} L{' '}
              {replayBar.bar.low.toFixed(2)} C {replayBar.bar.close.toFixed(2)}
            </span>
          </div>
        )}

        <StatusBadge source={source} live={live} wsStatus={wsStatus} mode={mode} />
      </div>
    </div>
  )
}
