/**
 * Pure replay state machine — deliberately framework-free so it is unit-testable.
 *
 * Cursor/total live in *tick* space: each bar spans ``stepsPerBar`` steps
 * (60 for a 1m replay at 1-second ticks; 1 = plain bar replay). A bar is
 * fully revealed once cursor passes its last tick, so the in-progress bar is
 * ``floor((cursor - 1) / stepsPerBar)``.
 *
 * State invariants:
 *   - cursor ∈ [0, total]
 *   - status 'playing' only while cursor < total; reaching total → 'completed'
 *   - seek/jump/reset always clamp into [0, total]
 */

export type ReplayStatus = 'idle' | 'playing' | 'paused' | 'completed'

export interface ReplayState {
  status: ReplayStatus
  /** Tick steps revealed (0-based, within [0, total]). */
  cursor: number
  /** Total tick steps loaded (bars × stepsPerBar). */
  total: number
  /** Ticks per bar (60 for 1m at 1-second ticks; 1 = plain bars). */
  stepsPerBar: number
  /** Tick steps revealed per interval tick (1x = one tick per second). */
  speed: number
  /** Where replay starts (reset/initial position, in bars). */
  startIndex: number
}

export const SPEEDS = [1, 2, 5, 10, 15, 20, 30, 60] as const
export type ReplaySpeed = (typeof SPEEDS)[number]

export type ReplayAction =
  | { type: 'load'; totalBars: number; stepsPerBar?: number }
  | { type: 'play' }
  | { type: 'pause' }
  | { type: 'resume' }
  | { type: 'tick' }
  | { type: 'seek'; index: number }
  | { type: 'reset' }
  | { type: 'jumpToLatest' }
  | { type: 'setSpeed'; speed: ReplaySpeed }

export const initialReplayState: ReplayState = {
  status: 'idle',
  cursor: 0,
  total: 0,
  stepsPerBar: 1,
  speed: 1,
  startIndex: 0,
}

const clamp = (index: number, total: number): number =>
  Math.max(0, Math.min(index, Math.max(total, 0)))

export function replayReducer(state: ReplayState, action: ReplayAction): ReplayState {
  switch (action.type) {
    case 'load': {
      const stepsPerBar = Math.max(1, Math.floor(action.stepsPerBar ?? 1))
      const total = Math.max(0, action.totalBars) * stepsPerBar
      // New dataset: keep the current cursor clamped, reset speed-dependent state
      return {
        ...state,
        total,
        stepsPerBar,
        cursor: clamp(state.cursor, total),
        startIndex: clamp(state.startIndex, total),
        status: total === 0 ? 'idle' : state.status,
      }
    }
    case 'play': {
      if (state.total === 0) return state
      // Playing from the end is a no-op (already complete)
      if (state.cursor >= state.total) return { ...state, status: 'completed' }
      return { ...state, status: 'playing' }
    }
    case 'pause':
      return state.status === 'playing' ? { ...state, status: 'paused' } : state
    case 'resume':
      return state.status === 'paused' ? { ...state, status: 'playing' } : state
    case 'tick': {
      if (state.status !== 'playing') return state
      const cursor = state.cursor + state.speed
      if (cursor >= state.total) {
        return { ...state, cursor: state.total, status: 'completed' }
      }
      return { ...state, cursor }
    }
    case 'seek': {
      const cursor = clamp(action.index, state.total)
      // Seeking to the end completes; otherwise keep current status but never
      // stay 'completed' on a backward seek.
      return {
        ...state,
        cursor,
        status: cursor >= state.total
          ? 'completed'
          : state.status === 'completed' ? 'idle' : state.status,
      }
    }
    case 'reset':
      return { ...state, cursor: state.startIndex, status: 'idle' }
    case 'jumpToLatest':
      return { ...state, cursor: state.total, status: state.total > 0 ? 'completed' : state.status }
    case 'setSpeed':
      return { ...state, speed: action.speed }
    default:
      return state
  }
}
