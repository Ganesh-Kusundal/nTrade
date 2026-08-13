import { describe, expect, it } from 'vitest'
import { loadJSON, saveJSON, type KVStorage } from '../storage'

function mockStorage(): KVStorage & { map: Map<string, string> } {
  const map = new Map<string, string>()
  return {
    map,
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
  }
}

describe('storage persistence', () => {
  it('round-trips a value through save + load', () => {
    const s = mockStorage()
    saveJSON('k', { a: 1, b: [true, null] }, s)
    expect(s.map.get('k')).toBe('{"a":1,"b":[true,null]}')
    expect(loadJSON('k', null, s)).toEqual({ a: 1, b: [true, null] })
  })

  it('returns the fallback for a missing key', () => {
    expect(loadJSON('missing', 'fallback', mockStorage())).toBe('fallback')
  })

  it('returns the fallback for corrupt JSON', () => {
    const s = mockStorage()
    s.map.set('bad', '{not json')
    expect(loadJSON('bad', 'fallback', s)).toBe('fallback')
  })

  it('returns the fallback when storage is unavailable (null)', () => {
    expect(loadJSON('k', 42, null)).toBe(42)
    expect(() => saveJSON('k', 42, null)).not.toThrow()
  })

  it('never throws when setItem fails (quota / private mode)', () => {
    const s: KVStorage = {
      getItem: () => null,
      setItem: () => { throw new Error('QuotaExceededError') },
    }
    expect(() => saveJSON('k', 1, s)).not.toThrow()
  })

  it('loads primitive values (numbers, strings)', () => {
    const s = mockStorage()
    saveJSON('n', 5, s)
    saveJSON('str', 'hi', s)
    expect(loadJSON('n', 0, s)).toBe(5)
    expect(loadJSON('str', '', s)).toBe('hi')
  })
})
