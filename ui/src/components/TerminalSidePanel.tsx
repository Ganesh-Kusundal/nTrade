import { PaperTradeControl } from './PaperTradeControl'
import { isMorningVahValResult, isValentiniResult } from '../lib/registry'
import type { ValentiniResult, ValentiniTrade } from '../lib/valentini'
import type { MorningVahValResult, MorningVahValTrade } from '../lib/morningVahVal'

function SideLabel({ children }: { children: React.ReactNode }) {
  return <span className="treadout-label">{children}</span>
}

function SideValue({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-sm text-ink">{children}</span>
}

function TradeRow({
  trade,
  index,
  open,
}: {
  trade: ValentiniTrade | MorningVahValTrade
  index: number
  open: boolean
}) {
  const sideCls = trade.side === 'BUY' ? 'text-accent' : 'text-danger'
  const held = open ? 'open' : `${trade.exitIndex == null ? 0 : trade.exitIndex - trade.entryIndex} bars`

  const reasonLabel = trade.reason ?? '—'
  const exit = trade.exit

  return (
    <div
      className={`rounded-md border border-line/60 bg-panel2/40 px-2.5 py-1.5 text-[11px] ${
        open ? 'border-accent/30 bg-accent/[0.04]' : 'border-line/40'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={`font-mono font-semibold ${sideCls}`}>
          {trade.side}
        </span>
        <span className="text-muted/70">#{index + 1}</span>
        <span className="text-muted/70">{held}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono">
        <span>E {trade.entry.toFixed(2)}</span>
        <span>SL {trade.sl.toFixed(2)}</span>
        {open && 'tslActive' in trade && trade.tslActive && trade.slNow != null && trade.slNow !== trade.sl && (
          <span className="text-amber-300/90">TSL {trade.slNow.toFixed(2)}</span>
        )}
        <span className="text-muted/70">
          {trade.tp != null ? `TP ${trade.tp.toFixed(2)}` : 'runner'}
        </span>
        {!open && exit != null && (
          <>
            <span>X {exit.toFixed(2)}</span>
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
  strategyResult: import('../components/ChartPanel').StrategyOverlay | null
  mode: string
}) {
  const morning = strategyResult && isMorningVahValResult(strategyResult)
  const valentini = strategyResult && isValentiniResult(strategyResult)

  const price = quote?.ltp ?? null
  const change = quote?.change ?? (price != null && quote ? price - quote.prev_close : 0)
  const changePct = quote?.change_pct ?? 0
  const up = (change ?? 0) >= 0

  return (
    <aside className="flex min-w-0 flex-col gap-2 overflow-y-auto px-2 py-2 sm:w-[300px]">
      {/* Quote */}
      <div className="tpanel px-3 py-2.5">
        <div className="mb-2">
          <SideLabel>Quote</SideLabel>
          <div className="mt-1 flex items-baseline gap-2">
            <SideValue>
              {price == null ? '—' : price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
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
              <span>Vol {quote?.volume != null ? quote.volume.toLocaleString('en-IN') : '—'}</span>
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
            {strategyId === 'morning_vah_val' ? 'VAH/VAL' : strategyId === 'valentini' ? 'Valentini' : strategyId}
          </SideLabel>
          <span className="text-[10px] text-muted/70">signals</span>
        </div>

        {/* status line — narrowed by guard */}
        {valentini && (() => {
          const vr = strategyResult as ValentiniResult
          return (
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] font-mono">
              <span className="text-muted">phase</span>
              <span
                className={`rounded-md border px-1.5 py-0.5 text-xs font-medium ${
                  vr.phase === 'signal'
                    ? 'border-accent/70 bg-accent/10 text-accent'
                    : vr.phase === 'accumulating'
                      ? 'border-amber-500/50 bg-amber-500/10 text-amber-300'
                      : 'border-line/60 bg-panel2/60 text-muted'
                }`}
              >
                {vr.phase}
              </span>
              {vr.lastAbsorption != null && (
                <span className="text-muted/70">
                  abs at {vr.lastAbsorption.barIndex}
                </span>
              )}
            </div>
          )
        })()}        {morning && (() => {
          const sr = strategyResult as MorningVahValResult
          const lastBias = sr.bias.length > 0 ? sr.bias[sr.bias.length - 1] : null
          const b = lastBias?.bias ?? '—'
          const cls = b === 'UP'
            ? 'border-accent/70 bg-accent/10 text-accent'
            : b === 'DOWN'
              ? 'border-danger/70 bg-danger/10 text-danger'
              : b === 'SIDEWAYS'
                ? 'border-amber-500/50 bg-amber-500/10 text-amber-300'
                : 'border-line/60 bg-panel2/60 text-muted'
          const lastLevels = sr.levels.length > 0 ? sr.levels[sr.levels.length - 1] : null
          const st = sr.state
          const setupKnown = st.profileReady && b !== '—'
          let statusText: string
          let statusCls = 'text-muted/80'
          if (!st.profileReady) {
            statusText = 'awaiting 09:30 FRVP freeze…'
            statusCls = 'text-amber-300'
          } else if (b === '—') {
            statusText = 'awaiting prior-day bias…'
            statusCls = 'text-amber-300'
          } else if (b === 'SIDEWAYS') {
            statusText = 'SIDEWAYS — no trades today'
          } else if (st.windowClosed) {
            statusText = 'entry window closed — no setup'
          } else if (!st.inEntryWindow) {
            statusText = 'setup window 09:30–11:00 IST'
          } else {
            statusText = b === 'UP' ? 'awaiting VAL dip + reversal (long)' : 'awaiting VAH rejection (short)'
            statusCls = 'text-amber-300'
          }
          return (
            <>
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] font-mono">
                <span className="text-muted">bias</span>
                <span className={`rounded-md border px-1.5 py-0.5 text-xs font-medium ${cls}`}>
                  {b}
                </span>
                {lastLevels && (
                  <>
                    <span className="text-muted/70">
                      POC {lastLevels.poc.toFixed(0)}
                    </span>
                    <span className="text-muted/70">
                      VAH {lastLevels.vah.toFixed(0)}
                    </span>
                    <span className="text-muted/70">
                      VAL {lastLevels.val.toFixed(0)}
                    </span>
                  </>
                )}
              </div>
              {setupKnown && (
                <div className="mb-2 flex items-center gap-1.5 text-[11px] font-mono">
                  <span className="text-muted">state</span>
                  <span className={statusCls}>{statusText}</span>
                </div>
              )}
            </>
          )
        })()}

        {/* trades list — union of trade shapes, narrowed field access */}
        {strategyResult && strategyResult.trades && strategyResult.trades.length > 0 && (
          <div className="mt-2 space-y-1.5">
            {strategyResult.trades.map((t, i) => (
              <TradeRow
                key={i}
                trade={t as ValentiniTrade}
                index={i}
                open={t.exitIndex == null}
              />
            ))}
          </div>
        )}
        {strategyResult && (!strategyResult.trades || strategyResult.trades.length === 0) && (
          <div className="mt-2 rounded-md border border-line/40 bg-panel2/30 px-2.5 py-1.5 text-[11px] text-muted/80">
            {strategyId === 'morning_vah_val'
              ? 'No signals yet — awaiting setup'
              : 'Awaiting absorption + VWAP signal…'}
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

function commandify(s: string | undefined): string {
  return s ? s.toUpperCase() : '—'
}
