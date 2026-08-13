import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'

/**
 * Minimal localStorage persistence for UI state (indicator toggles, replay
 * window, interval, range size) so a reload restores the session exactly.
 *
 * Storage is injectable (defaults to `window.localStorage`) so the pure
 * load/save round-trip is unit-testable in Node; every access is wrapped in
 * try/catch so a full/quarantined storage or a corrupt value can never crash
 * the app — it degrades to the fallback.
 */

export interface KVStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

const defaultStorage = (): KVStorage | null =>
  typeof window !== 'undefined' && window.localStorage ? window.localStorage : null

/** Read + parse a persisted value; `fallback` on missing / corrupt / unavailable. */
export function loadJSON<T>(key: string, fallback: T, storage: KVStorage | null = defaultStorage()): T {
  try {
    const raw = storage?.getItem(key)
    return raw == null ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

/** Serialize + persist a value. Best-effort: never throws. */
export function saveJSON<T>(key: string, value: T, storage: KVStorage | null = defaultStorage()): void {
  try {
    storage?.setItem(key, JSON.stringify(value))
  } catch {
    // storage full / unavailable — the session just won't persist
  }
}

/**
 * `useState` whose value is hydrated from localStorage and written back on
 * every change — the lazy way to make any selection state survive a reload.
 */
export function usePersistedState<T>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => loadJSON(key, initial))
  useEffect(() => {
    saveJSON(key, value)
  }, [key, value])
  return [value, setValue]
}
