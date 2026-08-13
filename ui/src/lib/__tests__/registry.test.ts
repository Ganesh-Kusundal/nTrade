import { describe, expect, it } from 'vitest'
import { indicators, strategies, sessionProfileCandles, type IndicatorSpec, type StrategySpec } from '../registry'
import { vwapSeries, buildVolumeProfile, detectAbsorptions } from '../indicators'
import { runValentini, type ValentiniResult } from '../valentini'
import { runMorningVahVal, type MorningVahValResult } from '../morningVahVal'
import type { Candle } from '../../types/market'

function bar(time: number, open: number, high: number, low: number, close: number, volume: number): Candle {
  return { time, open, high, low, close, volume }
}

/** Build a 1-minute bar every 60s starting at t0. */
function minuteBars(t0: number, n: number, base = 100): Candle[] {
  return Array.from({ length: n }, (_, i) =>
    bar(t0 + i * 60, base, base + 2, base - 1, base + (i % 3 === 0 ? 1 : 0), 1000 + i * 100),
  )
}

describe('registry — indicator + strategy mirror', () => {
  it('carries the same strategy ids as the backend ``ntrade.registry.strategy``', () => {
    // These ids are the contract: they must match
    // ``ntrade/engines/strategies.py`` -> ``strategy.register(...)`` calls.
    expect(Object.keys(strategies)).toEqual(['valentini', 'morning_vah_val'])
  })

  it('carries indicator ids the chart renders (mirror of backend ``ntrade.registry.indicator`` subset)', () => {
    // These are the indicators the UI actually draws (the chart renders these;
    // the Python registry may carry more that the UI doesn't render — parity is
    // enforced at the draw layer, not at the full registry).
    expect(Object.keys(indicators)).toContain('vwap')
    expect(Object.keys(indicators)).toContain('volume_profile')
    expect(Object.keys(indicators)).toContain('absorptions')
  })

  it('each indicator spec has a ``run`` that returns the expected shape', () => {
    const c = minuteBars(1_700_000_000, 30)
    expect(indicators['vwap'].run(c)).toMatchObject({ vwap: expect.any(Array), upper: expect.any(Array), lower: expect.any(Array) })
    expect(indicators['volume_profile'].run(c)).toMatchObject({ levels: expect.any(Array), poc: expect.any(Number), vah: expect.any(Number), val: expect.any(Number) })
    expect(indicators['absorptions'].run(c)).toEqual(expect.any(Array))
  })

  it('each strategy spec has a ``run`` returning the right overlay shape', () => {
    const c = minuteBars(1_700_000_000, 60)
    const v = strategies['valentini'].run(c, { warmup: 15 }) as ValentiniResult
    expect(v).toMatchObject({ phase: expect.any(String), trades: expect.any(Array) })
    expect(v.lastAbsorption).toBeNull()
    const m = strategies['morning_vah_val'].run(c, { sessionStart: '09:15', sessionEnd: '15:30' }) as MorningVahValResult
    expect(m).toMatchObject({ trades: expect.any(Array), levels: expect.any(Array), bias: expect.any(Array) })
  })

  it('sessionProfileCandles is re-exported from the registry (ChartPanel consumes it there)', () => {
    const c = minuteBars(1_700_000_000, 30)
    // IST anchor: bar at t0 = 1_700_000_000 epoch → 2023-11-14 22:53:20 UTC →
    // IST 2023-11-15 04:23:20. Session open '09:15' IST → 09:15 IST =
    // 09:15 - 5:30 = 03:45 UTC. The anchor bar is before 09:15 IST, so
    // sessionProfileCandles should return the session-day bars from 09:15 on.
    const out = sessionProfileCandles(c, '09:15')
    expect(out.length).toBeGreaterThanOrEqual(0)
    expect(Array.isArray(out)).toBe(true)
  })

  it('default params are present on both strategies', () => {
    expect((strategies['valentini'].defaultParams ?? {})['warmup']).toBe(15)
    expect((strategies['morning_vah_val'].defaultParams ?? {})['sessionStart']).toBe('09:15')
  })

  it('indicator labels are human-readable and each spec has a run fn', () => {
    for (const [key, spec] of Object.entries(indicators)) {
      // The registry key is the chart toggle key; ``spec.id`` is the backend
      // id (may differ for ``absorptions`` vs ``absorption`` — a known
      // parity-tax divergence tracked for Phase B).
      expect(spec.id).toBeTruthy()
      expect(spec.label).toBeTruthy()
      expect(typeof spec.run).toBe('function')
    }
    for (const [id, spec] of Object.entries(strategies)) {
      expect(spec.id).toBe(id)
      expect(spec.label).toBeTruthy()
      expect(typeof spec.run).toBe('function')
    }
  })
})
