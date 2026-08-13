/**
 * IST (Asia/Kolkata) time helpers.
 *
 * Wire convention: the API delivers every timestamp as a UTC epoch in seconds
 * (see `api/marketdata.py` — naive inputs mean IST wall time and are converted
 * to UTC on the wire). Rendering here is IST-deterministic so the UI never
 * depends on the browser's timezone:
 *
 *  - lightweight-charts v4 draws the time axis in UTC wall-clock (its tickmark
 *    formatter converts UTC components to a local date), so series times are
 *    shifted by +05:30 to make the axis display IST wall-clock.
 *  - every textual label uses `timeZone: 'Asia/Kolkata'` explicitly.
 */

/** IST is UTC+05:30 and has no DST — the fixed offset is exact. */
export const IST_OFFSET_S = 5.5 * 3600

/** Map a wire UTC epoch to the "chart epoch" lightweight-charts renders as IST wall-clock. */
export const istChartTime = (epochSeconds: number): number => epochSeconds + IST_OFFSET_S

interface FmtIstOptions {
  /** Include a 2-digit year, e.g. `04 Aug '26, 09:35`. */
  year?: boolean
  /** Include seconds, e.g. `04 Aug, 09:35:07`. */
  seconds?: boolean
}

/** IST wall date (YYYY-MM-DD) of a wire UTC epoch — the trading-day grouping key. */
export function istDateKey(epochSeconds: number): string {
  // Shift to IST wall-clock, then read UTC components (which now equal IST's).
  return new Date((epochSeconds + IST_OFFSET_S) * 1000).toISOString().slice(0, 10)
}

/** Format a wire UTC epoch as IST wall-clock, e.g. `04 Aug, 09:35`. TZ-independent. */
export function fmtIST(epochSeconds: number, opts: FmtIstOptions = {}): string {
  const dtf = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    ...(opts.year ? { year: '2-digit' as const } : {}),
    hour: '2-digit',
    minute: '2-digit',
    ...(opts.seconds ? { second: '2-digit' as const } : {}),
    hour12: false,
  })
  const parts = dtf.formatToParts(new Date(epochSeconds * 1000))
  const get = (type: Intl.DateTimeFormatPartTypes): string => parts.find((p) => p.type === type)?.value ?? ''
  const date = `${get('day')} ${get('month')}${opts.year ? ` '${get('year')}` : ''}`
  const time = `${get('hour')}:${get('minute')}${opts.seconds ? `:${get('second')}` : ''}`
  return `${date}, ${time}`
}

/**
 * Format a wire UTC epoch as a `<input type="datetime-local">` value in IST,
 * e.g. `2026-05-27T09:15`. datetime-local carries no timezone, so feeding it
 * IST wall-clock strings keeps the selection IST-deterministic on any machine.
 */
export function fmtISTInput(epochSeconds: number): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date(epochSeconds * 1000))
  const get = (type: Intl.DateTimeFormatPartTypes): string => parts.find((p) => p.type === type)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`
}

/** Format a wire UTC epoch as an IST wall-clock clock, e.g. `15:39:07`. */
export function fmtISTClock(epochSeconds: number): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(new Date(epochSeconds * 1000))
  const get = (type: Intl.DateTimeFormatPartTypes): string => parts.find((p) => p.type === type)?.value ?? ''
  return `${get('hour')}:${get('minute')}:${get('second')}`
}

/** Trading days the chart loads. Inclusive of today when today is a weekday. */
export const CHART_DAYS = 3

/**
 * Naive IST ISO for the candles `start` query: midnight of the IST date
 * `days` weekdays ago (inclusive of today). Weekend sessions are skipped so
 * Monday still shows three trading days.
 *
 * // ponytail: weekdays only; no holiday calendar — a holiday shrinks the window
 */
export function chartStartIst(nowEpochS: number, days: number = CHART_DAYS): string {
  const n = Math.max(1, days)
  let epoch = nowEpochS
  let kept = 0
  let startKey = istDateKey(epoch)
  // The backward scan needs enough calendar days to find `n` weekdays (5/7
  // ratio + weekend slack) — the old fixed 21-day cap silently truncated any
  // window wider than ~15 weekdays.
  const budget = Math.max(21, Math.ceil((n * 7) / 5) + 2)
  for (let i = 0; i < budget && kept < n; i++) {
    const key = istDateKey(epoch)
    const [y, mo, d] = key.split('-').map(Number)
    const dow = new Date(Date.UTC(y, mo - 1, d)).getUTCDay()
    if (dow !== 0 && dow !== 6) {
      kept++
      startKey = key
      if (kept >= n) break
    }
    epoch -= 86400
  }
  return `${startKey}T00:00:00`
}

/** Wire UTC epoch for `chartStartIst` — client clip floor matching the fetch `start` param. */
export function chartStartEpoch(nowEpochS?: number, days: number = CHART_DAYS): number {
  const now = nowEpochS ?? Date.now() / 1000
  return istInputToEpoch(chartStartIst(now, days)) ?? 0
}

/** Parse an IST datetime-local value back to a wire UTC epoch, or null if malformed. */
export function istInputToEpoch(value: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value)
  if (!m) return null
  const [, y, mo, d, h, mi] = m.map(Number)
  const utc = Date.UTC(y, mo - 1, d, h, mi)
  return Number.isFinite(utc) ? utc / 1000 - IST_OFFSET_S : null
}
