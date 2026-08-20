import { useCallback, useEffect, useRef } from 'react'
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp,
} from 'lightweight-charts'
import { indicators, strategies } from '../lib/registry'
import type { ChartOverlays, StrategyPayload } from '../types/market'
import { isMcxSession } from '../lib/marketHours'
import type { Candle } from '../types/market'
import { fmtIST, IST_OFFSET_S, istChartTime } from '../lib/istTime'

type CandleSeries = ISeriesApi<'Candlestick'>
type VolumeSeries = ISeriesApi<'Histogram'>
type LineSeries = ISeriesApi<'Line'>

export interface IndicatorToggles {
  vwap: boolean
  volumeProfile: boolean
  absorptions: boolean
  /** Strategy entries/exits (computed from the revealed bars). */
  strategy: boolean
}

interface ChartPanelProps {
  candles: Candle[]
  /** Full window rendered dimmed behind `candles` (TradingView-style replay
   *  context: the whole history is visible, bars ahead of the cursor ghosted). */
  context?: Candle[]
  /** Strategy indicator overlays (default: all on). */
  indicators?: IndicatorToggles
  /** Toggle handler for the on-chart legend (delegated from the page). */
  onIndicators?: (key: keyof IndicatorToggles) => void
  /** Server-computed overlays (VWAP ±σ, volume profile, absorptions).
   *  Single source of truth — the FE never recomputes them. */
  overlays?: ChartOverlays | null
  /** Server-computed strategy markers (signals + frozen VAH/VAL/POC levels). */
  strategy?: StrategyPayload | null
  /** Symbol + interval for the chart's aria-label (last OHLCV readout). */
  symbol?: string
  interval?: string
  /** Exchange + root for the session-anchored volume-profile range: MCX
   *  contracts profile from 09:00 IST, everything else from 09:15 IST. */
  exchange?: string
  root?: string
  className?: string
}

const UP = 'rgba(38, 166, 154, 0.45)'
const DOWN = 'rgba(239, 83, 80, 0.45)'
const DIM = 'rgba(148, 163, 184, 0.30)'
const DIM_VOL = 'rgba(148, 163, 184, 0.22)'
const VWAP = '#F59E0B'
const VWAP_BAND = 'rgba(245, 158, 11, 0.35)'

interface PrevRef {
  n: number
  lastTime: number
}

function bar(c: Candle): Parameters<CandleSeries['update']>[0] {
  return {
    time: istChartTime(c.time) as UTCTimestamp,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }
}

function volBar(c: Candle): Parameters<VolumeSeries['update']>[0] {
  return {
    time: istChartTime(c.time) as UTCTimestamp,
    value: c.volume,
    color: c.close >= c.open ? UP : DOWN,
  }
}

const shift = (p: { time: number; value: number | null }): { time: UTCTimestamp; value: number | null } => ({
  time: istChartTime(p.time) as UTCTimestamp,
  value: p.value,
})

/**
 * Candlestick + volume chart with backend-computed overlays: per-session VWAP
 * ± σ bands, volume profile (POC/VAH/VAL + left-edge histogram), absorption
 * markers, and strategy signals. All of this math is produced by the backend
 * OverlayPipeline (the same analytics + strategy code paper/live run) and sent
 * to the FE as plain data — the FE only renders it (zero-parity rule).
 */
export function ChartPanel({ candles, context, indicators: indicatorsProp, onIndicators, overlays, strategy, symbol, interval, exchange, root, className }: ChartPanelProps) {
  const toggles: IndicatorToggles = indicatorsProp ?? {
    vwap: true, volumeProfile: true, absorptions: true, strategy: true,
  }
  // On-chart legend — derived from the registry so adding an overlay only
  // requires registering its ``plot`` spec; no hardcoded list here.
  const LEGEND: { key: keyof IndicatorToggles; label: string }[] = [
    { key: 'vwap', label: indicators['vwap'].label },
    { key: 'volumeProfile', label: indicators['volume_profile'].label },
    { key: 'absorptions', label: indicators['absorptions'].label },
    { key: 'strategy', label: strategies['morning_vah_val'].label },
  ]
  const last = candles[candles.length - 1]
  const ariaLabel = last
    ? `${symbol ?? ''} ${interval ?? ''} · O ${last.open} H ${last.high} L ${last.low} C ${last.close}`
    : `${symbol ?? ''} ${interval ?? ''} chart`
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<CandleSeries | null>(null)
  const volRef = useRef<VolumeSeries | null>(null)
  const contextRef = useRef<CandleSeries | null>(null)
  const contextVolRef = useRef<VolumeSeries | null>(null)
  const vwapRef = useRef<LineSeries | null>(null)
  const vwapUpperRef = useRef<LineSeries | null>(null)
  const vwapLowerRef = useRef<LineSeries | null>(null)
  const vpPanelRef = useRef<HTMLDivElement | null>(null)
  const prevRef = useRef<PrevRef | null>(null)
  const vwapPrevRef = useRef<{ n: number; lastTime: number } | null>(null)
  const absMarkersRef = useRef<string>('')
  const stratLinesRef = useRef<IPriceLine[]>([])
  const levelLinesRef = useRef<IPriceLine[]>([])
  const profileLinesRef = useRef<IPriceLine[]>([])
  const profileOwnerRef = useRef<CandleSeries | null>(null)
  const profileLenRef = useRef(-1)
  const profileRafRef = useRef(0)
  const candlesRef = useRef(candles)
  const contextDataRef = useRef<Candle[] | undefined>(context)
  const indicatorsRef = useRef(toggles)
  const intervalRef = useRef(interval)
  const exchangeRef = useRef(exchange)
  const rootRef = useRef(root)
  const profileKeyRef = useRef('')
  const overlaysRef = useRef(overlays)
  const strategyRef = useRef(strategy)
  candlesRef.current = candles
  contextDataRef.current = context
  indicatorsRef.current = toggles
  intervalRef.current = interval
  exchangeRef.current = exchange
  rootRef.current = root
  overlaysRef.current = overlays
  strategyRef.current = strategy

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#0F172A' },
        textColor: '#94A3B8',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', ui-monospace, Menlo, monospace",
      },
      grid: {
        vertLines: { color: 'rgba(51, 65, 85, 0.35)' },
        horzLines: { color: 'rgba(51, 65, 85, 0.35)' },
      },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: '#475569' }, horzLine: { color: '#475569' } },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
      },
      localization: {
        timeFormatter: (time: UTCTimestamp) =>
          typeof time === 'number' ? fmtIST(time - IST_OFFSET_S, { year: true }) : String(time),
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
    })
    const candles = chart.addCandlestickSeries({
      upColor: '#26A69A',
      downColor: 'transparent',
      borderUpColor: '#26A69A',
      borderDownColor: '#EF5350',
      wickUpColor: '#26A69A',
      wickDownColor: '#EF5350',
    })
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    const contextCandles = chart.addCandlestickSeries({
      upColor: DIM, downColor: DIM, borderUpColor: DIM, borderDownColor: DIM, wickUpColor: DIM, wickDownColor: DIM,
    })
    const contextVolume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: 'volume',
    })
    const vwap = chart.addLineSeries({
      color: VWAP, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false,
    })
    const bandOpts = {
      color: VWAP_BAND, lineWidth: 1 as const, lineStyle: LineStyle.Dashed,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    }
    const vwapUpper = chart.addLineSeries(bandOpts)
    const vwapLower = chart.addLineSeries(bandOpts)
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    chartRef.current = chart
    candleRef.current = candles
    volRef.current = volume
    contextRef.current = contextCandles
    contextVolRef.current = contextVolume
    vwapRef.current = vwap
    vwapUpperRef.current = vwapUpper
    vwapLowerRef.current = vwapLower
    return () => {
      if (profileRafRef.current) window.cancelAnimationFrame(profileRafRef.current)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volRef.current = null
      contextRef.current = null
      contextVolRef.current = null
      vwapRef.current = null
      vwapUpperRef.current = null
      vwapLowerRef.current = null
      prevRef.current = null
      vwapPrevRef.current = null
      stratLinesRef.current = []
      levelLinesRef.current = []
      profileLinesRef.current = []
      profileOwnerRef.current = null
    }
  }, [])

  useEffect(() => {
    const ctx = context ?? []
    const cs = contextRef.current
    const vs = contextVolRef.current
    if (!chartRef.current || !cs || !vs) return
    cs.setData(ctx.map(bar))
    vs.setData(ctx.map((c) => ({ time: istChartTime(c.time) as UTCTimestamp, value: c.volume, color: DIM_VOL })))
    if (ctx.length > 0) chartRef.current.timeScale().fitContent()
  }, [context])

  // --- server overlays ------------------------------------------------

  useEffect(() => {
    const v = vwapRef.current
    const u = vwapUpperRef.current
    const l = vwapLowerRef.current
    if (!v || !u || !l) return
    const ov = overlaysRef.current
    if (!toggles.vwap || !ov || !ov.vwap) {
      v.setData([]); u.setData([]); l.setData([]); vwapPrevRef.current = null
      return
    }
    const series = ov.vwap
    const last = series[series.length - 1]
    const lastTime = last ? last.time : -1
    const prev = vwapPrevRef.current
    // VWAP ±σ bands: the backend sends a single end-of-session value (one
    // non-null point), so render them as a constant line across the whole
    // window — a band pinned only at the last bar would be a single dot.
    const bandValueAt = (p: { time: number; value: number | null } | undefined): number | null => {
      if (!p || p.value == null) return null
      return p.value
    }
    const upperConst = bandValueAt(ov.vwap_upper?.[ov.vwap_upper.length - 1])
    const lowerConst = bandValueAt(ov.vwap_lower?.[ov.vwap_lower.length - 1])
    // Band only valid from the first in-session bar (where VWAP starts).
    const firstValid = series.findIndex((p) => p.value != null)
    const bandSpan = firstValid >= 0 ? series.slice(firstValid) : []
    const uppers = upperConst != null && bandSpan.length > 0
      ? bandSpan.map((p) => ({ time: p.time, value: upperConst }))
      : []
    const lowers = lowerConst != null && bandSpan.length > 0
      ? bandSpan.map((p) => ({ time: p.time, value: lowerConst }))
      : []
    // VWAP is undefined before the session opens (backend sends value:null
    // for those bars). lightweight-charts rejects null in a line series, so
    // map nulls to whitespace (gap) rather than {time, value:null}.
    const vwapData = series.map((p) =>
      p.value == null
        ? { time: istChartTime(p.time) as UTCTimestamp }
        : { time: istChartTime(p.time) as UTCTimestamp, value: p.value as number })
    if (prev && series.length > 0 && series.length === prev.n && lastTime === prev.lastTime) {
      if (last.value != null) v.update({ time: istChartTime(last.time) as UTCTimestamp, value: last.value as number })
      if (uppers.length) v.update(shift(uppers[uppers.length - 1]))
      if (lowers.length) v.update(shift(lowers[lowers.length - 1]))
    } else {
      v.setData(vwapData)
      u.setData(uppers.map(shift))
      l.setData(lowers.map(shift))
    }
    vwapPrevRef.current = { n: series.length, lastTime }
  }, [overlays, toggles.vwap])

  // Absorption + strategy markers — both come from the server.
  useEffect(() => {
    const cs = candleRef.current
    if (!cs) return
    const src = candlesRef.current
    const timeOf = (t: number) => {
      const idx = src.findIndex((c) => c.time === t)
      if (idx >= 0) return istChartTime(src[idx].time) as UTCTimestamp
      return istChartTime(t) as UTCTimestamp
    }
    const markers: SeriesMarker<UTCTimestamp>[] = []
    const ov = overlaysRef.current
    if (toggles.absorptions && ov?.absorptions) {
      for (const a of ov.absorptions) {
        markers.push({
          time: timeOf(a.time),
          position: a.side === 'BUY' ? 'belowBar' : 'aboveBar',
          shape: a.side === 'BUY' ? 'arrowUp' : 'arrowDown',
          color: a.side === 'BUY' ? '#26A69A' : '#EF5350',
        })
      }
    }
    const strat = strategyRef.current
    if (toggles.strategy && strat?.signals) {
      for (const s of strat.signals) {
        const t = s.reference_price ?? s.intent_price ?? 0
        const label = s.exit_reason ? s.exit_reason.toUpperCase() : s.side
        markers.push({
          time: timeOf(t),
          position: s.side === 'BUY' ? 'belowBar' : 'aboveBar',
          shape: s.exit_reason ? 'square' : 'circle',
          color: s.exit_reason === 'target' ? '#26A69A'
            : s.exit_reason === 'stop' ? '#EF5350' : s.side === 'BUY' ? '#26A69A' : '#EF5350',
          text: label,
        })
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    const key = JSON.stringify(markers)
    if (key !== absMarkersRef.current) {
      absMarkersRef.current = key
      cs.setMarkers(markers)
    }
  }, [overlays, strategy, toggles.absorptions, toggles.strategy])

  // Open-trade SL/TP price lines from the latest strategy signal pair.
  useEffect(() => {
    const cs = candleRef.current
    if (!cs) return
    for (const line of stratLinesRef.current) cs.removePriceLine(line)
    stratLinesRef.current = []
    if (!toggles.strategy || !strategy) return
    const sigs = strategy.signals
    const entries = sigs.filter((s) => !s.exit_reason)
    const open = entries[entries.length - 1]
    if (!open) return
    if (open.sl != null) {
      stratLinesRef.current.push(cs.createPriceLine({
        price: open.sl, color: '#EF5350', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'SL', axisLabelVisible: true,
      }))
    }
    if (open.tp != null) {
      stratLinesRef.current.push(cs.createPriceLine({
        price: open.tp, color: '#26A69A', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'TP', axisLabelVisible: true,
      }))
    }
  }, [strategy, toggles.strategy])

  // Frozen morning VAH/VAL/POC lines.
  useEffect(() => {
    const cs = candleRef.current
    if (!cs) return
    for (const line of levelLinesRef.current) cs.removePriceLine(line)
    levelLinesRef.current = []
    if (!toggles.strategy || !strategy?.levels || strategy.levels.length === 0) return
    const lvl = strategy.levels[strategy.levels.length - 1]
    if (!(lvl.vah > lvl.val && lvl.vah > 0)) return
    levelLinesRef.current = [
      cs.createPriceLine({ price: lvl.vah, color: 'rgba(245, 158, 11, 0.8)', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'VAH', axisLabelVisible: true }),
      cs.createPriceLine({ price: lvl.val, color: 'rgba(148, 163, 184, 0.8)', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'VAL', axisLabelVisible: true }),
    ]
  }, [strategy, toggles.strategy])

  // Volume profile histogram + POC/VAH/VAL lines — from the server payload.
  const renderProfile = useCallback(() => {
    const chart = chartRef.current
    const cs = candleRef.current
    const panel = vpPanelRef.current
    if (!chart || !cs || !panel) return
    const owner = profileOwnerRef.current
    if (owner) for (const line of profileLinesRef.current) owner.removePriceLine(line)
    profileOwnerRef.current = null
    profileLinesRef.current = []
    panel.innerHTML = ''
    if (!indicatorsRef.current.volumeProfile) return
    const ov = overlaysRef.current
    const vp = ov?.volume_profile
    if (!vp || vp.levels.length === 0) return
    const maxVol = Math.max(...vp.levels.map((l) => l.volume))
    const panelW = Math.max(panel.clientWidth, 16)
    panel.style.bottom = `${chart.timeScale().height() || 24}px`
    const barH = 3
    const frag = document.createDocumentFragment()
    for (const level of vp.levels) {
      if (level.volume <= 0) continue
      const y = cs.priceToCoordinate(level.price)
      if (y == null) continue
      const div = document.createElement('div')
      const w = Math.max(2, (level.volume / maxVol) * panelW)
      const isPoc = level.price === vp.poc
      div.style.cssText =
        `position:absolute;left:0;top:${y - barH / 2}px;height:${barH}px;width:${w}px;` +
        `background:${isPoc ? 'rgba(245,158,11,0.9)' : 'rgba(148,163,184,0.4)'};border-radius:1px;`
      frag.appendChild(div)
    }
    panel.appendChild(frag)
    const makeLine = (price: number, color: string, lineStyle: LineStyle, title: string): IPriceLine =>
      cs.createPriceLine({ price, color, lineWidth: 1, lineStyle, title, axisLabelVisible: true })
    profileLinesRef.current = [
      makeLine(vp.poc, VWAP, LineStyle.Solid, 'POC'),
      makeLine(vp.vah, 'rgba(148,163,184,0.7)', LineStyle.Dashed, 'VAH'),
      makeLine(vp.val, 'rgba(148,163,184,0.7)', LineStyle.Dashed, 'VAL'),
    ]
    profileOwnerRef.current = cs
  }, [])

  const scheduleProfile = useCallback(() => {
    if (profileRafRef.current) return
    profileRafRef.current = window.requestAnimationFrame(() => {
      profileRafRef.current = 0
      renderProfile()
    })
  }, [renderProfile])

  useEffect(() => {
    const chart = chartRef.current
    const el = containerRef.current
    if (!chart || !el) return
    const ro = new ResizeObserver(scheduleProfile)
    ro.observe(el)
    return () => ro.disconnect()
  }, [scheduleProfile])

  useEffect(() => {
    scheduleProfile()
  }, [toggles.volumeProfile, overlays, scheduleProfile])

  // --- candle sync --------------------------------------------------------

  useEffect(() => {
    const chart = chartRef.current
    const candleSeries = candleRef.current
    const volSeries = volRef.current
    if (!chart || !candleSeries || !volSeries) return

    if (candles.length === 0) {
      candleSeries.setData([])
      volSeries.setData([])
      prevRef.current = null
      return
    }

    const last = candles[candles.length - 1]
    const prev = prevRef.current

    if (prev == null) {
      candleSeries.setData(candles.map(bar))
      volSeries.setData(candles.map(volBar))
      chart.timeScale().fitContent()
    } else if (last.time === prev.lastTime && candles.length === prev.n) {
      candleSeries.update(bar(last))
      volSeries.update(volBar(last))
    } else if (candles.length > prev.n && candles[prev.n].time > prev.lastTime) {
      for (let i = prev.n; i < candles.length; i++) {
        candleSeries.update(bar(candles[i]))
        volSeries.update(volBar(candles[i]))
      }
    } else {
      candleSeries.setData(candles.map(bar))
      volSeries.setData(candles.map(volBar))
      chart.timeScale().fitContent()
    }
    prevRef.current = { n: candles.length, lastTime: last.time }

    const ctx = contextDataRef.current
    const srcLen = ctx && ctx.length > 0 ? ctx.length : candles.length
    const profileKey = `${symbol ?? ''}|${interval ?? ''}|${exchange ?? ''}|${root ?? ''}`
    if (profileKey !== profileKeyRef.current || srcLen !== profileLenRef.current) {
      profileKeyRef.current = profileKey
      profileLenRef.current = srcLen
      scheduleProfile()
    }
  }, [candles, scheduleProfile])

  return (
    <div className={`relative ${className ?? 'h-full w-full'}`} aria-label={ariaLabel}>
      {onIndicators && (
        <div className="pointer-events-auto absolute left-2 top-2 z-20 flex flex-wrap items-center gap-1.5">
          {LEGEND.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              className={`tpill ${toggles[key] ? 'tpill-active' : ''}`}
              onClick={() => onIndicators(key)}
              aria-pressed={toggles[key]}
            >
              {label}
            </button>
          ))}
          {toggles.volumeProfile && (
            <span className="treadout-label hidden sm:inline">
              Sess VP {isMcxSession(exchange, root) ? '09:00' : '09:15'} · POC · VAH · VAL
            </span>
          )}
        </div>
      )}
      <div ref={containerRef} className="h-full w-full" />
      <div
        ref={vpPanelRef}
        className="pointer-events-none absolute left-0 top-0 z-10 w-16 overflow-hidden"
        aria-hidden="true"
      />
    </div>
  )
}
