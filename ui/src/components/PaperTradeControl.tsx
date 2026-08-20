import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type PaperStatus } from '../api/client'
import { fmtIST } from '../lib/istTime'

/**
 * Paper-trading control — starts/stops the HalfTrend paper session on
 * the selected symbol (₹1M paper capital, zero real orders) and shows the
 * live account state: balance, equity, realized/unrealized PnL, the open
 * position, and the recent fill tape.
 */
export function PaperTradeControl({ symbol, exchange, disabled }: {
  symbol: string
  exchange: string
  disabled?: boolean
}) {
  const [status, setStatus] = useState<PaperStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [show, setShow] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const running = status?.running === true

  const refresh = useCallback(() => {
    api.paperStatus()
      .then((s) => {
        setStatus(s)
        setError(null)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  // Poll only while the panel is visible or a session is running — never
  // churn /api/paper/status for a collapsed, idle control.
  useEffect(() => {
    if (!show && !running) return
    refresh()
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = setInterval(refresh, 2500)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [refresh, show, running])

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const s = await api.paperStart(symbol, exchange)
      setStatus(s)
      setShow(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    setBusy(true)
    setError(null)
    try {
      const s = await api.paperStop()
      setStatus(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const pos = status?.positions?.[0]
  const pnl = (status?.realized_pnl ?? 0) + (status?.unrealized_pnl ?? 0)

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        className={`tpill ${show ? 'tpill-active' : ''}`}
        onClick={() => setShow((v) => !v)}
        aria-expanded={show}
        title="HalfTrend paper trading on the selected symbol (₹1M paper capital)"
      >
        {running ? '● Paper Live' : 'Paper Trade'}
      </button>
      {show && (
        <div className="tpanel flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-line/60 bg-panel2/60 px-3 py-2 text-[11px]">
          <span className="treadout-label">
            {running ? (
              <span className="font-semibold text-teal-300">● trading {status.symbol}</span>
            ) : (
              <span className="text-muted">idle — no paper session</span>
            )}
          </span>
          {running && status && (
            <>
              <span title="Paper account cash">
                Bal <b className="text-slate-200">{status.balance.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b>
              </span>
              <span title="Cash + open-position MTM">
                Eq <b className="text-slate-200">{status.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b>
              </span>
              <span title={`Realized ${status.realized_pnl} + unrealized ${status.unrealized_pnl}`}>
                PnL <b className={pnl >= 0 ? 'text-teal-300' : 'text-red-400'}>{pnl >= 0 ? '+' : ''}{pnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b>
              </span>
              {pos && (
                <span title={`${pos.symbol} avg ${pos.avg_price} @ ${pos.ltp}`}>
                  Pos <b className="text-slate-200">{pos.quantity > 0 ? '+' : ''}{pos.quantity}</b>
                </span>
              )}
              <span>
                Fills <b className="text-slate-200">{status.n_trades}</b>
              </span>
              {status.started_at && (
                <span className="text-muted/70">since {fmtIST(new Date(status.started_at).getTime() / 1000)}</span>
              )}
              <button
                type="button"
                className="tpill text-red-400 hover:text-red-300"
                onClick={stop}
                disabled={busy}
                title="Stop the paper session (flattens via the strategy's close rules)"
              >
                Stop
              </button>
            </>
          )}
          {!running && (
            <>
              <button
                type="button"
                className="tpill tpill-active"
                onClick={start}
                disabled={busy || disabled || !symbol}
                title={`Start paper trading ${symbol || 'the selected contract'} with ₹1M paper capital`}
              >
                {busy ? 'Starting…' : 'Start'}
              </button>
              {disabled && <span className="text-muted/70">select a contract first</span>}
            </>
          )}
          {error && <span className="text-red-400" title={error}>API error</span>}
        </div>
      )}
    </div>
  )
}
