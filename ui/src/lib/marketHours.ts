/** Exchange session windows for the Fabio UI strategy (IST). */

export type SessionWindow = { start: string; end: string }

/**
 * Curated MCX commodity roots (mirrors ``SUPPORTED_ROOTS`` commodities in
 * ``api/marketdata.py``). Used as a belt-and-suspenders fallback when the
 * contract's ``exchange`` field is missing/stale — without this, SILVERM
 * (etc.) silently inherit the NSE 15:25 hard-close and the END marker
 * fires mid-afternoon while the evening session is still live.
 */
const MCX_ROOTS = new Set([
  'CRUDEOIL', 'CRUDEOILM', 'GOLD', 'GOLDM', 'SILVER', 'SILVERM',
])

/** True when the exchange tag or the root symbol maps to the MCX session. */
export function isMcxSession(exchange?: string | null, root?: string | null): boolean {
  if (String(exchange ?? '').toUpperCase() === 'MCX') return true
  const r = String(root ?? '').toUpperCase().split(/\s+/)[0] // "SILVERM AUG FUT" → SILVERM
  return MCX_ROOTS.has(r)
}

/** Hard session-close is 5 minutes before the exchange close (NSE 15:30 / MCX 23:30). */
export function strategySession(
  exchange?: string | null,
  root?: string | null,
): SessionWindow {
  if (isMcxSession(exchange, root)) {
    return { start: '09:00', end: '23:25' }
  }
  return { start: '09:15', end: '15:25' }
}
