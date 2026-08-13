import {
  ChevronsRight,
  Pause,
  Play,
  RotateCcw,
  SkipForward,
} from 'lucide-react'
import { SPEEDS, type ReplayState } from '../hooks/replayReducer'
import { fmtIST } from '../lib/istTime'
import type { Candle } from '../types/market'

interface ReplayControlsProps {
  state: ReplayState
  candles: Candle[]
  /** datetime-local values (IST) shown in the From/To window inputs. */
  fromValue: string
  toValue: string
  onFromChange: (value: string) => void
  onToChange: (value: string) => void
  onPlay: () => void
  onPause: () => void
  onResume: () => void
  onSeek: (index: number) => void
  onReset: () => void
  onJumpToLatest: () => void
  onSpeed: (speed: (typeof SPEEDS)[number]) => void
}

export function ReplayControls({
  state,
  candles,
  fromValue,
  toValue,
  onFromChange,
  onToChange,
  onPlay,
  onPause,
  onResume,
  onSeek,
  onReset,
  onJumpToLatest,
  onSpeed,
}: ReplayControlsProps) {
  const playing = state.status === 'playing'
  const hasData = state.total > 0
  const completed = state.status === 'completed'
  const steps = Math.max(1, state.stepsPerBar)
  // Cursor is in tick steps; the in-progress bar is floor((cursor-1)/stepsPerBar).
  const barIndex = state.cursor > 0
    ? Math.min(Math.floor((state.cursor - 1) / steps), candles.length - 1)
    : -1
  const lastVisible = barIndex >= 0 ? candles[barIndex] : undefined
  const revealed = barIndex >= 0 ? state.cursor - barIndex * steps : 0
  const midBar = revealed > 0 && revealed < steps
  // Seek slider operates on bar starts; the value snaps so dragging can never
  // land mid-bar (partial intrabar positions are read via the OHLC strip).
  const snapToBar = (v: number) => Math.floor(v / steps) * steps

  return (
    <div className="flex flex-col gap-2.5 border-t border-line/60 bg-panel/80 px-3 py-2.5">
      {/* replay window: playback is confined to this slice of history */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="treadout-label">Replay window</span>
        <input
          type="datetime-local"
          className="tinput"
          value={fromValue}
          onChange={(e) => onFromChange(e.target.value)}
          disabled={!hasData}
          aria-label="Replay window start"
        />
        <span className="text-[11px] text-muted">→</span>
        <input
          type="datetime-local"
          className="tinput"
          value={toValue}
          onChange={(e) => onToChange(e.target.value)}
          disabled={!hasData}
          aria-label="Replay window end"
        />
        <span className="hidden text-[11px] text-muted/70 sm:inline" title="Playback runs only over this window of history; the chart shows just the selected bars">
          replay plays only this slice
        </span>
      </div>

      {/* transport row */}
      <div className="flex flex-wrap items-center gap-2">
        {playing ? (
          <button type="button" className="tbtn tbtn-primary" onClick={onPause} aria-label="Pause replay">
            <Pause className="h-3.5 w-3.5" /> Pause
          </button>
        ) : (
          <button
            type="button"
            className="tbtn tbtn-primary"
            onClick={state.status === 'paused' ? onResume : onPlay}
            disabled={!hasData || completed}
            aria-label="Play replay"
          >
            <Play className="h-3.5 w-3.5" /> {state.status === 'paused' ? 'Resume' : 'Play'}
          </button>
        )}

        <div className="flex items-center gap-1" role="group" aria-label="Replay speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              className={`tpill ${state.speed === s ? 'tpill-active' : ''}`}
              onClick={() => onSpeed(s)}
              aria-pressed={state.speed === s}
            >
              {s}x
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button type="button" className="tbtn" onClick={onReset} disabled={!hasData} aria-label="Reset replay">
            <RotateCcw className="h-3.5 w-3.5" /> Reset
          </button>
          <button
            type="button"
            className="tbtn"
            onClick={onJumpToLatest}
            disabled={!hasData}
            aria-label="Jump to latest"
          >
            <ChevronsRight className="h-3.5 w-3.5" /> Latest
          </button>
        </div>
      </div>

      {/* seek row */}
      <div className="flex items-center gap-3">
        <input
          type="range"
          className="treplay"
          min={0}
          max={Math.max(state.total, 1)}
          step={steps}
          value={snapToBar(state.cursor)}
          disabled={!hasData}
          onChange={(e) => onSeek(snapToBar(Number(e.target.value)))}
          aria-label="Seek through loaded history"
        />
        <span className="whitespace-nowrap font-mono text-[11px] text-muted">
          {state.cursor.toLocaleString()} / {state.total.toLocaleString()}
          <span className="ml-2 hidden sm:inline">
            · {lastVisible ? fmtIST(lastVisible.time) : '—'}
          </span>
        </span>
        {/* Paused mid-bar: the chart shows the partial (animated) bar, so
            surface the real bar's OHLCV + intra-bar progress here. */}
        {!playing && state.cursor > 0 && state.cursor < state.total && barIndex >= 0 && (
          <span
            className="hidden whitespace-nowrap font-mono text-[11px] text-muted/80 md:inline"
            title="Real bar values — the intra-bar path is simulated from this bar's OHLCV"
          >
            Bar {barIndex + 1}/{candles.length} · O {candles[barIndex].open.toFixed(2)} H{' '}
            {candles[barIndex].high.toFixed(2)} L {candles[barIndex].low.toFixed(2)} C{' '}
            {candles[barIndex].close.toFixed(2)} · V {candles[barIndex].volume.toLocaleString('en-IN')}
            {midBar && ` · tick ${revealed}/${steps}`}
          </span>
        )}
        {state.stepsPerBar > 1 && (
          <span className="hidden whitespace-nowrap text-[11px] text-muted/70 sm:inline" title="Intra-bar movement is synthesized from each bar's OHLCV (1-second ticks)">
            simulated 1s ticks
          </span>
        )}
        {completed && (
          <span className="inline-flex items-center gap-1 whitespace-nowrap rounded border border-accent/50 bg-accent/10 px-2 py-0.5 text-[11px] font-medium text-accent">
            <SkipForward className="h-3 w-3" /> Replay complete
          </span>
        )}
      </div>
    </div>
  )
}
