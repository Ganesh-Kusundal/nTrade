import { useEffect, useReducer } from 'react'
import {
  initialReplayState,
  replayReducer,
  type ReplayAction,
  type ReplaySpeed,
  type ReplayState,
} from './replayReducer'

/** Base wall-time per 1x reveal step (ms). 1x = one 1-second tick per second
 * — real-time market pace. Speed divides this. */
export const BASE_TICK_MS = 1000

export interface UseReplayResult {
  state: ReplayState
  dispatch: React.Dispatch<ReplayAction>
  play: () => void
  pause: () => void
  resume: () => void
  seek: (index: number) => void
  reset: () => void
  jumpToLatest: () => void
  setSpeed: (speed: ReplaySpeed) => void
  progressPct: number
}

/**
 * Chart-replay control: progressively reveal loaded candles, one tick step
 * at a time (``stepsPerBar`` ticks per bar when tick replay is enabled, else
 * one step per bar).
 *
 * The timer lives only while ``status === 'playing'`` and is torn down on
 * unmount / pause / speed change — no timer leaks, no stray ticks after the
 * component is gone.
 */
export function useReplay(totalBars: number, stepsPerBar = 1): UseReplayResult {
  const [state, dispatch] = useReducer(replayReducer, initialReplayState)

  // Sync with the loaded dataset (contract / interval switch resets nothing
  // beyond clamping — the caller re-arms via reset/play on mode entry).
  useEffect(() => {
    dispatch({ type: 'load', totalBars, stepsPerBar })
  }, [totalBars, stepsPerBar])

  useEffect(() => {
    if (state.status !== 'playing') return
    const id = setInterval(() => dispatch({ type: 'tick' }), BASE_TICK_MS / state.speed)
    return () => clearInterval(id)
  }, [state.status, state.speed])

  const progressPct = state.total > 0 ? Math.round((state.cursor / state.total) * 100) : 0

  return {
    state,
    dispatch,
    play: () => dispatch({ type: 'play' }),
    pause: () => dispatch({ type: 'pause' }),
    resume: () => dispatch({ type: 'resume' }),
    seek: (index: number) => dispatch({ type: 'seek', index }),
    reset: () => dispatch({ type: 'reset' }),
    jumpToLatest: () => dispatch({ type: 'jumpToLatest' }),
    setSpeed: (speed: ReplaySpeed) => dispatch({ type: 'setSpeed', speed }),
    progressPct,
  }
}
