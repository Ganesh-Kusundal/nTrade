import { describe, expect, it } from 'vitest'
import { chartStartEpoch, chartStartIst, fmtIST, fmtISTClock, fmtISTInput, IST_OFFSET_S, istChartTime, istDateKey, istInputToEpoch } from '../istTime'

describe('istTime', () => {
  it('uses the fixed +05:30 offset', () => {
    expect(IST_OFFSET_S).toBe(19800)
    // 2026-08-07 10:09 UTC = 15:39 IST
    expect(istChartTime(1786097340)).toBe(1786097340 + 19800)
  })

  it('formats wire epochs as IST wall-clock, independent of browser timezone', () => {
    expect(fmtIST(1786097340)).toBe('07 Aug, 15:39') // 10:09 UTC → 15:39 IST
    expect(fmtIST(0)).toBe('01 Jan, 05:30') // epoch 0 = 00:00 UTC = 05:30 IST
  })

  it('optionally includes the year and seconds', () => {
    expect(fmtIST(1786097340, { year: true })).toBe("07 Aug '26, 15:39")
    expect(fmtIST(1786097340, { seconds: true })).toBe('07 Aug, 15:39:00')
  })

  it('round-trips through the chart shift: shifted epoch formatted in IST equals true IST', () => {
    const wire = 1786097340
    expect(fmtIST(istChartTime(wire) - IST_OFFSET_S, { year: true })).toBe("07 Aug '26, 15:39")
  })

  it('groups trading days by IST wall date (a 00:00 IST bar is the same day, not the previous UTC day)', () => {
    // 2026-08-07 18:30 UTC = 2026-08-08 00:00 IST — must key as Aug 8.
    expect(istDateKey(Date.UTC(2026, 7, 7, 18, 30) / 1000)).toBe('2026-08-08')
    // Mid-session IST bars keep their IST date.
    expect(istDateKey(Date.UTC(2026, 7, 7, 3, 45) / 1000)).toBe('2026-08-07') // 09:15 IST
    expect(istDateKey(Date.UTC(2026, 7, 7, 10, 0) / 1000)).toBe('2026-08-07') // 15:30 IST
  })

  it('round-trips datetime-local input values in IST', () => {
    const epoch = 1786097340 // 2026-08-07 15:39 IST
    expect(fmtISTInput(epoch)).toBe('2026-08-07T15:39')
    expect(istInputToEpoch('2026-08-07T15:39')).toBe(epoch)
    // Parse interprets the string as IST wall time, not browser-local.
    expect(istInputToEpoch('2026-05-27T09:15')).toBe(Date.UTC(2026, 4, 27, 9, 15) / 1000 - IST_OFFSET_S)
    expect(istInputToEpoch('garbage')).toBeNull()
    expect(istInputToEpoch('')).toBeNull()
  })

  it('formats an IST wall-clock clock (HH:MM:SS)', () => {
    expect(fmtISTClock(1786097340)).toBe('15:39:00') // 10:09 UTC → 15:39:00 IST
    expect(fmtISTClock(0)).toBe('05:30:00') // epoch 0 = 00:00 UTC = 05:30 IST
  })

  it('chartStartIst is midnight IST of the Nth weekday, inclusive of today', () => {
    // Wed 12 Aug 2026 → Mon 10 (3 weekdays: Mon, Tue, Wed)
    expect(chartStartIst(istInputToEpoch('2026-08-12T11:47')!, 3)).toBe('2026-08-10T00:00:00')
    // Monday skips the weekend → Thu 6, Fri 7, Mon 10
    expect(chartStartIst(istInputToEpoch('2026-08-10T10:00')!, 3)).toBe('2026-08-06T00:00:00')
    // Sunday is not a session — last 3 weekdays are Fri 7, Thu 6, Wed 5
    expect(chartStartIst(istInputToEpoch('2026-08-09T12:00')!, 3)).toBe('2026-08-05T00:00:00')
  })

  it('chartStartEpoch matches chartStartIst wire floor', () => {
    const now = istInputToEpoch('2026-08-12T11:47')!
    expect(chartStartEpoch(now, 3)).toBe(istInputToEpoch('2026-08-10T00:00:00'))
  })

  it('chartStartIst scales past the old 21-calendar-day scan cap', () => {
    // 20 weekdays back from Wed 12 Aug 2026 = Thu 16 Jul 2026. The old fixed
    // 21-calendar-day scan stopped at ~15 weekdays (Jul 23) and silently
    // truncated any wider window.
    expect(chartStartIst(istInputToEpoch('2026-08-12T11:47')!, 20)).toBe('2026-07-16T00:00:00')
    // The 3-day default is unchanged.
    expect(chartStartIst(istInputToEpoch('2026-08-12T11:47')!, 3)).toBe('2026-08-10T00:00:00')
  })
})
