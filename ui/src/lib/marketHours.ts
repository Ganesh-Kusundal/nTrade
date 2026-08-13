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

/** True when the root is an MCX commodity — from the live backend roots list
 *  when given, else the curated legacy fallback. */
export function isMcxRoot(root: string | null | undefined, roots?: string[]): boolean {
  if (!root) return false
  const r = String(root).toUpperCase().split(/\s+/)[0] // "SILVERM AUG FUT" → SILVERM
  if (roots && roots.length > 0) return roots.some((x) => String(x).toUpperCase().split(/\s+/)[0] === r)
  return MCX_ROOTS.has(r)
}

/** True when the exchange tag or the root symbol maps to the MCX session. */
export function isMcxSession(exchange?: string | null, root?: string | null, roots?: string[]): boolean {
  if (String(exchange ?? '').toUpperCase() === 'MCX') return true
  return isMcxRoot(root, roots)
}

/** Hard session-close is 5 minutes before the exchange close (NSE 15:30 / MCX 23:30). */
export function strategySession(
  exchange?: string | null,
  root?: string | null,
  roots?: string[],
): SessionWindow {
  if (isMcxSession(exchange, root, roots)) {
    return { start: '09:00', end: '23:25' }
  }
  return { start: '09:15', end: '15:25' }
}
