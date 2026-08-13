import { describe, expect, it } from 'vitest'
import { initialReplayState, replayReducer } from '../replayReducer'

function loaded(totalBars: number, stepsPerBar = 1): ReturnType<typeof replayReducer> {
  return replayReducer({ ...initialReplayState }, { type: 'load', totalBars, stepsPerBar })
}

describe('replayReducer', () => {
  it('loads a dataset and clamps out-of-range cursor', () => {
    let s = loaded(100)
    expect(s.total).toBe(100)
    expect(s.stepsPerBar).toBe(1)
    s = replayReducer({ ...s, cursor: 50 }, { type: 'load', totalBars: 10 })
    expect(s.cursor).toBe(10)
    expect(s.total).toBe(10)
  })

  it('load with stepsPerBar expands total into tick space', () => {
    const s = loaded(10, 60)
    expect(s.total).toBe(600)
    expect(s.stepsPerBar).toBe(60)
    // Degenerate inputs degrade to bar replay, never zero/NaN
    expect(replayReducer(initialReplayState, { type: 'load', totalBars: 5, stepsPerBar: 0 }).stepsPerBar).toBe(1)
  })

  it('tick reveals stepsPerBar ticks before the next bar', () => {
    let s = loaded(2, 60)
    s = replayReducer(s, { type: 'play' })
    for (let i = 0; i < 60; i++) s = replayReducer(s, { type: 'tick' })
    expect(s.cursor).toBe(60) // bar 0 fully revealed
    s = replayReducer(s, { type: 'tick' })
    expect(s.cursor).toBe(61) // first tick of bar 1
  })

  it('play is a no-op on an empty dataset', () => {
    expect(replayReducer(loaded(0), { type: 'play' })).toEqual(loaded(0))
  })

  it('tick advances by speed while playing', () => {
    let s = loaded(100)
    s = replayReducer(s, { type: 'play' })
    expect(s.status).toBe('playing')
    s = replayReducer(s, { type: 'tick' })
    expect(s.cursor).toBe(1)
    s = replayReducer(s, { type: 'setSpeed', speed: 5 })
    s = replayReducer(s, { type: 'tick' })
    expect(s.cursor).toBe(6)
  })

  it('tick does not advance when paused or idle', () => {
    let s = replayReducer(loaded(100), { type: 'play' })
    s = replayReducer(s, { type: 'pause' })
    expect(s.status).toBe('paused')
    s = replayReducer(s, { type: 'tick' })
    expect(s.cursor).toBe(0)
  })

  it('reaching the end completes and stops', () => {
    let s = loaded(4)
    s = replayReducer(s, { type: 'setSpeed', speed: 10 })
    s = replayReducer(s, { type: 'play' })
    s = replayReducer(s, { type: 'tick' })
    expect(s.status).toBe('completed')
    expect(s.cursor).toBe(4)
    // further ticks are inert
    const again = replayReducer(s, { type: 'tick' })
    expect(again.cursor).toBe(4)
    expect(again.status).toBe('completed')
  })

  it('resume returns from paused to playing', () => {
    let s = replayReducer(loaded(100), { type: 'play' })
    s = replayReducer(s, { type: 'pause' })
    s = replayReducer(s, { type: 'resume' })
    expect(s.status).toBe('playing')
    s = replayReducer(s, { type: 'resume' }) // resume while playing is inert
    expect(s.status).toBe('playing')
  })

  it('seek clamps to bounds and completes at the end', () => {
    let s = replayReducer(loaded(100), { type: 'seek', index: 50 })
    expect(s.cursor).toBe(50)
    s = replayReducer(s, { type: 'seek', index: 999 })
    expect(s.cursor).toBe(100)
    expect(s.status).toBe('completed')
  })

  it('seek clamps to the tick-space total', () => {
    let s = replayReducer(loaded(3, 60), { type: 'seek', index: 999 })
    expect(s.cursor).toBe(180)
    expect(s.status).toBe('completed')
    s = replayReducer(s, { type: 'seek', index: 61 })
    expect(s.cursor).toBe(61)
    expect(s.status).toBe('idle')
  })

  it('seeking backwards from completed resets to idle', () => {
    const s = replayReducer({ ...loaded(100), status: 'completed' as const, cursor: 100 }, { type: 'seek', index: 20 })
    expect(s.cursor).toBe(20)
    expect(s.status).toBe('idle')
  })

  it('reset returns to the start index, idle', () => {
    let s = replayReducer(loaded(100), { type: 'seek', index: 40 })
    s = replayReducer(s, { type: 'play' })
    s = replayReducer(s, { type: 'tick' })
    s = replayReducer(s, { type: 'reset' })
    expect(s.cursor).toBe(0)
    expect(s.status).toBe('idle')
  })

  it('jumpToLatest completes at the last tick', () => {
    const s = replayReducer(loaded(250, 60), { type: 'jumpToLatest' })
    expect(s.cursor).toBe(15000)
    expect(s.status).toBe('completed')
  })

  it('speed updates preserve state', () => {
    let s = replayReducer(loaded(100), { type: 'play' })
    s = replayReducer(s, { type: 'setSpeed', speed: 10 })
    expect(s.speed).toBe(10)
    expect(s.status).toBe('playing')
  })
})
