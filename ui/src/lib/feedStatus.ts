/** Feed / session truth helpers — single source of truth for status that
 *  the chrome used to fake with a hardcoded green dot. */

import { isMcxSession } from './marketHours'
import { IST_OFFSET_S } from './istTime'

export type FeedKind =
  | 'replay' // replay mode, no live feed required
  | 'streaming' // live provider + WS connected
  | 'reconnecting' // live provider, WS trying to reconnect
  | 'offline' // live provider, WS disconnected
  | 'historical' // provider has no live feed (paper / seed) — historical only

export type WsStatus = 'connected' | 'disconnected' | 'reconnecting' | 'off'

/**
 * Derive the one feed status the whole chrome should show. Replay wins;
 * otherwise a non-live provider (paper/seed) or a socket that was never
 * opened is historical-only, and a live provider maps connected /
 * reconnecting / disconnected (note: 'off' is historical, not 'offline' —
 * 'offline' is reserved for an attempted-but-failed live socket).
 */
export function feedKind(mode: 'live' | 'replay', live: boolean, wsStatus: WsStatus): FeedKind {
  if (mode === 'replay') return 'replay'
  if (!live || wsStatus === 'off') return 'historical'
  if (wsStatus === 'connected') return 'streaming'
  if (wsStatus === 'reconnecting') return 'reconnecting'
  return 'offline'
}

export const FEED_META: Record<FeedKind, { label: string; dot: string; text: string }> = {
  streaming: { label: 'streaming', dot: 'bg-accent', text: 'text-accent' },
  reconnecting: { label: 'reconnecting…', dot: 'bg-amber-400 animate-pulse', text: 'text-amber-300' },
  offline: { label: 'feed offline', dot: 'bg-danger', text: 'text-danger' },
  historical: { label: 'historical', dot: 'bg-slate-400', text: 'text-muted' },
  replay: { label: 'replay', dot: 'bg-slate-400', text: 'text-muted' },
}

// ponytail: weekday-only session window. No NSE/MCX holiday calendar exists
// in the repo, so this deliberately ignores exchange holidays (known ceiling:
// a major holiday would read OPEN; upgrade path = wire in an instrument
// master holiday list when one exists).
export interface SessionWindow {
  start: string // "HH:MM" IST
  end: string // "HH:MM" IST
}

/**
 * Exchange trading hours — distinct from Fabio's `strategySession`, which
 * ends 5 minutes early (15:25 / 23:25) so positions never cross the close.
 * This is the real session edge used only for the OPEN/CLOSED chip.
 */
export function exchangeSession(exchange?: string | null, root?: string | null): SessionWindow {
  if (isMcxSession(exchange, root)) {
    return { start: '09:00', end: '23:30' }
  }
  return { start: '09:15', end: '15:30' }
}

/** Parse "HH:MM" IST to minutes since local IST midnight. */
function hmToMinutes(hm: string): number {
  const [h, m] = hm.split(':').map(Number)
  return h * 60 + m
}

/**
 * True when the exchange is open at the given wire UTC epoch (IST wall clock).
 * Closed on Saturday/Sunday; closed before `start` and after `end` (inclusive
 * of the closing minute so 15:30 still reads OPEN).
 */
export function isSessionOpen(
  exchange?: string | null,
  root?: string | null,
  epochSeconds?: number,
): boolean {
  const now = epochSeconds ?? Math.floor(Date.now() / 1000)
  const ist = new Date((now + IST_OFFSET_S) * 1000)
  const day = ist.getUTCDay() // 0 Sun … 6 Sat
  if (day === 0 || day === 6) return false
  const mins = ist.getUTCHours() * 60 + ist.getUTCMinutes()
  const { start, end } = exchangeSession(exchange, root)
  return mins >= hmToMinutes(start) && mins <= hmToMinutes(end)
}
