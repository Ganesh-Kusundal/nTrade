import { PaperTradeControl } from './PaperTradeControl'
import { fmtPrice, fmtVolume } from '../lib/format'
import { strategyLabel } from '../lib/registry'
import type { StrategyPayload, StrategySignal } from '../types/market'

function SideLabel({ children }: { children: React.ReactNode }) {
  return <span className="treadout-label">{children}</span>
}

function SideValue({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-sm text-ink">{children}</span>
}

/** Pair flat entry/exit signals into trade rows for display (no client math):
 *  each entry opens a position; the next signal with an `exit_reason` closes it.
 *  Matching is positional — the backend emits them in bar order. */
interface SignalTrade {
  side: 'BUY' | 'SELL'
  entry: number
  sl: number | null
  tp: number | null
  exit: number | null
  exitReason: string | null
  qty: number
}

function toTrades(signals: StrategySignal[]): SignalTrade[] {
  const trades: SignalTrade[] = []
  const open: SignalTrade[] = []
  for (const s of signals) {
    if (s.exit_reason) {
      const t = open.shift()
      if (t) {
        t.exit = s.intent_price ?? s.reference_price ?? null
        t.exitReason = s.exit_reason
        trades.push(t)
      }
    } else {
      open.push({
        side: s.side,
        entry: s.reference_price ?? s.intent_price ?? 0,
        sl: s.sl ?? null,
        tp: s.tp ?? null,
        exit: null,
        exitReason: null,
        qty: s.quantity,
      })
    }
  }
  // Any still-open positions render as open trades.
  for (const t of open) trades.push(t)
  return trades
}

function TradeRow({ trade, index, open }: { trade: SignalTrade; index: number; open: boolean }) {
  const sideCls = trade.side === 'BUY' ? 'text-accent' : 'text-danger'
  const reasonLabel = trade.exitReason ?? '—'
  return (
    <div
      className={`rounded-md border border-line/60 bg-panel2/40 px-2.5 py-1.5 text-[11px] ${
        open ? 'border-accent/30 bg-accent/[0.04]' : 'border-line/40'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={`font-mono font-semibold ${sideCls}`}>{trade.side}</span>
        <span className="text-muted/70">#{index + 1}</span>
        <span className="text-muted/70">{open ? 'open' : 'closed'}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono">
        <span>E {trade.entry.toFixed(2)}</span>
        {trade.sl != null && <span>SL {trade.sl.toFixed(2)}</span>}
        {trade.tp != null && trade.tp > 0 && <span>TP {trade.tp.toFixed(2)}</span>}
        {!open && trade.exit != null && (
          <>
            <span>X {trade.exit.toFixed(2)}</span>
            <span className="text-muted/70">{reasonLabel}</span>
          </>
        )}
      </div>
    </div>
  )
}

export function TerminalSidePanel({
  quote,
  contract,
  symbol,
  exchange,
  strategyId,
  strategyResult,
  mode,
}: {
  quote: import('../types/market').Quote | null
  contract: import('../types/market').Contract | null
  symbol: string
  exchange: string
  strategyId: string
  strategyResult: StrategyPayload | null
  mode: string
}) {
  const price = quote?.ltp ?? null
  const change = quote?.change ?? (price != null && quote ? price - quote.prev_close : 0)
  const changePct = quote?.change_pct ?? 0
  const up = (change ?? 0) >= 0

  const trades = strategyResult?.signals ? toTrades(strategyResult.signals) : []
  const open = trades.filter((t) => t.exit == null).length
  const lastLevel = strategyResult?.levels?.[strategyResult.levels.length - 1]
  const bias = strategyResult?.bias ?? null
  const phase = strategyResult?.phase ?? null

  return (
    <aside className="flex min-w-0 flex-col gap-2 overflow-y-auto px-2 py-2 sm:w-[300px]">
      {/* Quote */}
      <div className="tpanel px-3 py-2.5">
        <div className="mb-2">
          <SideLabel>Quote</SideLabel>
          <div className="mt-1 flex items-baseline gap-2">
            <SideValue>
              {price == null ? '—' : fmtPrice(price)}
            </SideValue>
            {price != null && change != null && (
              <span className={`font-mono text-sm ${up ? 'text-accent' : 'text-danger'}`}>
                {up ? '+' : ''}{change.toFixed(2)} ({up ? '+' : ''}{changePct.toFixed(2)}%)
              </span>
            )}
          </div>
          {contract && (
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] font-mono text-muted">
              <span>O {quote?.open != null ? quote.open.toFixed(2) : '—'}</span>
              <span>H {quote?.high != null ? quote.high.toFixed(2) : '—'}</span>
              <span>L {quote?.low != null ? quote.low.toFixed(2) : '—'}</span>
              <span>Prev {quote?.prev_close != null ? quote.prev_close.toFixed(2) : '—'}</span>
              <span>Vol {quote?.volume != null ? fmtVolume(quote.volume) : '—'}</span>
            </div>
          )}
        </div>
        {contract && (
          <div className="mt-2 pt-2 border-t border-line/40 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] font-mono text-muted">
            <span>{commandify(contract.root)}</span>
            <span>{commandify(exchange)}</span>
            <span>Lot {contract.lot_size}</span>
            <span>Tick {contract.tick_size}</span>
            {contract.is_front_month && <span className="text-accent">Front</span>}
          </div>
        )}
      </div>

      {/* Paper account */}
      <div className="tpanel px-3 py-2.5">
        <div className="flex items-center justify-between">
          <SideLabel>Paper</SideLabel>
          <span className="text-[10px] text-muted/70">{mode === 'live' ? 'live' : 'replay'}</span>
        </div>
        <PaperTradeControl symbol={symbol} exchange={exchange} disabled={!symbol} />
      </div>

      {/* Strategy signals */}
      <div className="tpanel px-3 py-2.5">
        <div className="flex items-center justify-between">
          <SideLabel>
            {strategyLabel(strategyId)}
          </SideLabel>
          <span className="text-[10px] text-muted/70">signals{open > 0 ? ` · ${open} open` : ''}</span>
        </div>

        {strategyResult && (
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] font-mono">
            {bias && (
              <>
                <span className="text-muted">bias</span>
                <span className={`rounded-md border px-1.5 py-0.5 text-xs font-medium ${biasCls(bias)}`}>
                  {bias}
                </span>
              </>
            )}
            {phase && (
              <>
                <span className="text-muted">phase</span>
                <span className={`rounded-md border px-1.5 py-0.5 text-xs font-medium ${phaseCls(phase)}`}>
                  {phase}
                </span>
              </>
            )}
            {lastLevel && (
              <>
                <span className="text-muted/70">POC {lastLevel.poc.toFixed(0)}</span>
                <span className="text-muted/70">VAH {lastLevel.vah.toFixed(0)}</span>
                <span className="text-muted/70">VAL {lastLevel.val.toFixed(0)}</span>
              </>
            )}
          </div>
        )}

        {trades.length > 0 && (
          <div className="mt-2 space-y-1.5">
            {trades.map((t, i) => (
              <TradeRow key={i} trade={t} index={i} open={t.exit == null} />
            ))}
          </div>
        )}
        {strategyResult && trades.length === 0 && (
          <div className="mt-2 rounded-md border border-line/40 bg-panel2/30 px-2.5 py-1.5 text-[11px] text-muted/80">
            Awaiting signal…
          </div>
        )}

        {!strategyResult && (
          <div className="mt-2 rounded-md border border-line/40 bg-panel2/30 px-2.5 py-1.5 text-[11px] text-muted/80">
            No data for this window yet.
          </div>
        )}
      </div>

      {/* keyboard hint */}
      <div className="rounded-md border border-line/40 bg-panel2/30 px-2.5 py-1.5 text-[10px] font-mono text-muted leading-relaxed">
        <span className="text-muted/60">keys </span>
        <span className="text-accent/80">1-5</span> int ·
        <span className="text-accent/80">0</span> range ·
        <span className="text-accent/80">L</span> live ·
        <span className="text-accent/80">P</span> replay ·
        <span className="text-accent/80">Space</span> play
      </div>
    </aside>
  )
}

function biasCls(b: string): string {
  if (b === 'UP') return 'border-accent/70 bg-accent/10 text-accent'
  if (b === 'DOWN') return 'border-danger/70 bg-danger/10 text-danger'
  if (b === 'SIDEWAYS') return 'border-amber-500/50 bg-amber-500/10 text-amber-300'
  return 'border-line/60 bg-panel2/60 text-muted'
}

function phaseCls(p: string): string {
  if (p === 'signal') return 'border-accent/70 bg-accent/10 text-accent'
  if (p === 'reversal') return 'border-amber-500/50 bg-amber-500/10 text-amber-300'
  return 'border-line/60 bg-panel2/60 text-muted'
}

function commandify(s: string | undefined): string {
  return s ? s.toUpperCase() : '—'
}
