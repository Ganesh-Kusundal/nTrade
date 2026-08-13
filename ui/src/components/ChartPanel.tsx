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
import { indicators, strategies, sessionProfileCandles } from '../lib/registry'
import type { VwapSeriesResult, VolumeProfile, Absorption, VpLevel, VwapPoint } from '../lib/indicators'
import { isMcxSession } from '../lib/marketHours'
import type { Candle } from '../types/market'
import { fmtIST, IST_OFFSET_S, istChartTime } from '../lib/istTime'

// v4 series handles: addCandlestickSeries / addHistogramSeries / addLineSeries return these.
type CandleSeries = ISeriesApi<'Candlestick'>
type VolumeSeries = ISeriesApi<'Histogram'>
type LineSeries = ISeriesApi<'Line'>

/** Trade shape both strategy overlays expose (Valentini + Morning VAH/VAL). */
export interface StrategyTrade {
  side: 'BUY' | 'SELL'
  entryIndex: number
  sl: number
  tp: number | null
  exitIndex: number | null
  exit: number | null
  reason: string | null
  partialIndex?: number | null
  partial?: number | null
}

export interface StrategyOverlay {
  trades: StrategyTrade[]
  /** Frozen morning VAH/VAL/POC per IST day (Morning VAH/VAL strategy). */
  levels?: Array<{ date: string; vah: number; val: number; poc: number }>
}

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
  /** Completed revealed bars for the strategy state machine — the strategy
   *  reacts to closed bars, so this is `candles` without the in-progress one.
   *  Precomputed in the page so it stays stable across intra-bar ticks. */
  strategyCandles?: Candle[]
  /** Precomputed strategy result (null → computed from strategyCandles). */
  strategy?: StrategyOverlay | null
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
// Dimmed (ahead-of-cursor / context) palette — neutral slate at low alpha.
const DIM = 'rgba(148, 163, 184, 0.30)'
const DIM_VOL = 'rgba(148, 163, 184, 0.22)'
// Valentini overlay palette: VWAP / POC amber, value area slate.
const VWAP = '#F59E0B'
const VWAP_BAND = 'rgba(245, 158, 11, 0.35)'

// Direct references to the registry-wired indicator runners (stable per app
// lifetime — the registry is the single source of truth, so adding a new
// indicator only requires registering it; ChartPanel reads it here by key).
const VWAP_RUN = indicators['vwap'].run as (candles: Candle[]) => VwapSeriesResult
const ABSORB_RUN = indicators['absorptions'].run as (candles: Candle[]) => Absorption[]
const VP_RUN = indicators['volume_profile'].run as (candles: Candle[], step?: number) => VolumeProfile

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

const shift = (p: VwapPoint): { time: UTCTimestamp; value: number } => ({
  time: istChartTime(p.time) as UTCTimestamp,
  value: p.value,
})

/**
 * Candlestick + volume chart with optional Valentini (Fabio) overlays:
 * per-session VWAP ± σ bands, volume profile (POC/VAH/VAL price lines +
 * a horizontal histogram on the left edge), and absorption markers.
 *
 * Data syncing is incremental: a trailing update (same last time, or one
 * appended bar) uses ``series.update()`` (O(1) — smooth replay/live); any
 * other change (seek, interval/contract switch) does a full ``setData()``
 * and refits the time scale. The chart is removed on unmount.
 */
export function ChartPanel({ candles, context, indicators: indicatorsProp, onIndicators, strategyCandles, strategy, symbol, interval, exchange, root, className }: ChartPanelProps) {
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
  candlesRef.current = candles
  contextDataRef.current = context
  indicatorsRef.current = toggles
  intervalRef.current = interval
  exchangeRef.current = exchange
  rootRef.current = root

  // One chart for the component lifetime; removed on unmount (no leak).
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
      // Series times are shifted by +05:30 (see istChartTime) so the v4 axis
      // — drawn in UTC wall-clock — shows IST. The crosshair receives the same
      // shifted epoch, so undo the shift and format the true instant in IST.
      localization: {
        timeFormatter: (time: UTCTimestamp) =>
          typeof time === 'number' ? fmtIST(time - IST_OFFSET_S, { year: true }) : String(time),
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
    })
    const candles = chart.addCandlestickSeries({
      upColor: '#26A69A',
      // Hollow bearish so direction reads without relying on color alone
      // (colorblind-safe: filled up vs outline down).
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
    // Dimmed context series (rendered first → drawn beneath the live series).
    const contextCandles = chart.addCandlestickSeries({
      upColor: DIM,
      downColor: DIM,
      borderUpColor: DIM,
      borderDownColor: DIM,
      wickUpColor: DIM,
      wickDownColor: DIM,
    })
    const contextVolume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    // Valentini overlays: VWAP line + ±σ band lines (dashed, translucent).
    const vwap = chart.addLineSeries({
      color: VWAP,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
    })
    const bandOpts = {
      color: VWAP_BAND,
      lineWidth: 1 as const,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    }
    const vwapUpper = chart.addLineSeries(bandOpts)
    const vwapLower = chart.addLineSeries(bandOpts)
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    })
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

  // Context (dimmed background) is static for the lifetime of its dataset —
  // set once per change, never per replay tick. Declared before the candles
  // effect so the first fitContent sees the full window. Refit on change
  // (entering replay / editing the window) — a plain setData keeps the stale
  // viewport, and the foreground is empty at cursor 0 so nothing else refits.
  useEffect(() => {
    const chart = chartRef.current
    const cs = contextRef.current
    const vs = contextVolRef.current
    if (!chart || !cs || !vs) return
    const ctx = context ?? []
    cs.setData(ctx.map(bar))
    vs.setData(ctx.map((c) => ({
      time: istChartTime(c.time) as UTCTimestamp,
      value: c.volume,
      color: DIM_VOL,
    })))
    if (ctx.length > 0) chart.timeScale().fitContent()
  }, [context])

  // --- Valentini overlays ------------------------------------------------

  // VWAP + bands: incremental like the candles — an in-progress bar tick only
  // revises the last point, so update in place instead of rebuilding.
  useEffect(() => {
    const v = vwapRef.current
    const u = vwapUpperRef.current
    const l = vwapLowerRef.current
    if (!v || !u || !l) return
    if (!toggles.vwap) {
      v.setData([])
      u.setData([])
      l.setData([])
      vwapPrevRef.current = null
      return
    }
    const data = VWAP_RUN(candles)
    const last = data.vwap[data.vwap.length - 1]
    const lastTime = last ? last.time : -1
    const prev = vwapPrevRef.current
    // In-place update only when both series are non-empty and the last point
    // is genuinely the same bar (a 0===0 && -1===-1 empty-series match must
    // not reach update(undefined)).
    if (prev && data.vwap.length > 0 && data.vwap.length === prev.n && lastTime === prev.lastTime) {
      v.update(shift(last))
      u.update(shift(data.upper[data.upper.length - 1]))
      l.update(shift(data.lower[data.lower.length - 1]))
    } else {
      v.setData(data.vwap.map(shift))
      u.setData(data.upper.map(shift))
      l.setData(data.lower.map(shift))
    }
    vwapPrevRef.current = { n: data.vwap.length, lastTime }
  }, [candles, toggles.vwap])

  // Absorption + strategy markers on the candle series. Absorption: BUY
  // below-bar arrows / SELL above. Strategy: circles at entries (BUY below /
  // SELL above), squares at exits (colored by reason) — both share the
  // marker layer, so they are merged into one set.
  useEffect(() => {
    const cs = candleRef.current
    if (!cs) return
    const src = strategyCandles ?? candles
    const markers: SeriesMarker<UTCTimestamp>[] = []
    if (toggles.absorptions) {
      for (const a of ABSORB_RUN(src)) {
        if (a.barIndex < 0 || a.barIndex >= src.length) continue
        markers.push({
          time: istChartTime(src[a.barIndex].time) as UTCTimestamp,
          position: a.side === 'BUY' ? 'belowBar' : 'aboveBar',
          shape: a.side === 'BUY' ? 'arrowUp' : 'arrowDown',
          color: a.side === 'BUY' ? '#26A69A' : '#EF5350',
        })
      }
    }
    if (toggles.strategy && strategy) {
      for (const t of strategy.trades) {
        if (t.entryIndex < 0 || t.entryIndex >= src.length) continue
        markers.push({
          time: istChartTime(src[t.entryIndex].time) as UTCTimestamp,
          position: t.side === 'BUY' ? 'belowBar' : 'aboveBar',
          shape: 'circle',
          color: t.side === 'BUY' ? '#26A69A' : '#EF5350',
          text: t.side === 'BUY' ? 'BUY' : 'SELL',
        })
        // T1 partial book (Morning VAH/VAL: 50% at the opposite VA level).
        if (t.partialIndex != null && t.partialIndex >= 0 && t.partialIndex < src.length) {
          markers.push({
            time: istChartTime(src[t.partialIndex].time) as UTCTimestamp,
            position: t.side === 'BUY' ? 'aboveBar' : 'belowBar',
            shape: 'square',
            color: '#F59E0B',
            text: '½',
          })
        }
        if (t.exitIndex != null && t.reason && t.exitIndex >= 0 && t.exitIndex < src.length) {
          markers.push({
            time: istChartTime(src[t.exitIndex].time) as UTCTimestamp,
            position: t.side === 'BUY' ? 'aboveBar' : 'belowBar',
            shape: 'square',
            color: t.reason === 'target' ? '#26A69A' : t.reason === 'stop' ? '#EF5350' : '#F59E0B',
            text: t.reason === 'target' ? 'TP' : t.reason === 'stop' ? 'SL' : 'END',
          })
        }
      }
    }
    // lightweight-charts requires markers ascending by time; absorptions +
    // strategy trades are merged from independent walks so they can interleave.
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    const key = JSON.stringify(markers)
    if (key !== absMarkersRef.current) {
      absMarkersRef.current = key
      cs.setMarkers(markers)
    }
  }, [strategyCandles ?? candles, strategy, toggles.absorptions, toggles.strategy])

  // Open-trade SL/TP price lines (strategy overlay) — dashed lines at the
  // live trade's stop and target, removed when the trade closes / toggled off.
  useEffect(() => {
    const cs = candleRef.current
    if (!cs) return
    for (const line of stratLinesRef.current) cs.removePriceLine(line)
    stratLinesRef.current = []
    if (!toggles.strategy || !strategy) return
    const open = strategy.trades[strategy.trades.length - 1]
    if (!open || open.exitIndex != null) return
    stratLinesRef.current = [
      cs.createPriceLine({ price: open.sl, color: '#EF5350', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'SL', axisLabelVisible: true }),
    ]
    if (open.tp != null) {
      stratLinesRef.current.push(
        cs.createPriceLine({ price: open.tp, color: '#26A69A', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'TP', axisLabelVisible: true }),
      )
    }
  }, [strategy, toggles.strategy])

  // Frozen morning VAH/VAL lines (Morning VAH/VAL strategy) — the static
  // levels the strategy trades against, drawn for the most recent frozen day
  // so they don't stack across days. Cleared when the overlay is off.
  useEffect(() => {
    const cs = candleRef.current
    if (!cs) return
    for (const line of levelLinesRef.current) cs.removePriceLine(line)
    levelLinesRef.current = []
    if (!toggles.strategy || !strategy || !strategy.levels || strategy.levels.length === 0) return
    const lvl = strategy.levels[strategy.levels.length - 1]
    if (!(lvl.vah > lvl.val && lvl.vah > 0)) return
    levelLinesRef.current = [
      cs.createPriceLine({ price: lvl.vah, color: 'rgba(245, 158, 11, 0.8)', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'VAH', axisLabelVisible: true }),
      cs.createPriceLine({ price: lvl.val, color: 'rgba(148, 163, 184, 0.8)', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'VAL', axisLabelVisible: true }),
    ]
  }, [strategy, toggles.strategy])

  // Volume profile: horizontal histogram on the left edge (aligned via
  // priceToCoordinate) + POC/VAH/VAL price lines, over a FIXED range — the
  // current session day anchored at its session open (MCX 09:00, NSE 09:15),
  // the TradingView FRVP behaviour. Zooming/panning does NOT rebuild it; it
  // only grows as the live session prints bars (data-length change / resize /
  // toggle rebuild it).
  //
  // Data source: in replay mode the full window (`context`, the dimmed
  // background) — the profile is then STABLE while the cursor advances,
  // instead of growing as bars are revealed. Live mode falls back to the
  // candles (no context). The 1D interval's bars sit at midnight IST (before
  // the session open), so daily charts profile the whole loaded window.
  const renderProfile = useCallback(() => {
    const chart = chartRef.current
    const cs = candleRef.current
    const panel = vpPanelRef.current
    if (!chart || !cs || !panel) return
    // Price lines must be removed from the series they were created on — which
    // is the context series in replay (see `coord` below). Track the owner.
    const owner = profileOwnerRef.current
    if (owner) for (const line of profileLinesRef.current) owner.removePriceLine(line)
    profileOwnerRef.current = null
    profileLinesRef.current = []
    panel.innerHTML = ''
    if (!indicatorsRef.current.volumeProfile) return
    const candles = candlesRef.current
    const ctx = contextDataRef.current
    const src = ctx && ctx.length > 0 ? ctx : candles
    if (src.length === 0) return
    const isMcx = isMcxSession(exchangeRef.current, rootRef.current)
    const profSrc = intervalRef.current === '1D'
      ? src
      : sessionProfileCandles(src, isMcx ? '09:00' : '09:15')
    if (profSrc.length === 0) return
    const vp = VP_RUN(profSrc)
    // Coordinate source: at replay cursor 0 the candle series is empty (no
    // revealed bars) and its priceToCoordinate returns null for every price,
    // even though the shared price scale has data. The context series always
    // holds the full window and sits on the same price scale, so use it for
    // both the histogram alignment and the POC/VAH/VAL price lines.
    const coord = ctx && ctx.length > 0 && contextRef.current ? contextRef.current : cs
    if (vp.levels.length === 0) return
    const maxVol = Math.max(...vp.levels.map((l: VpLevel) => l.volume))
    const panelW = Math.max(panel.clientWidth, 16)
    panel.style.bottom = `${chart.timeScale().height() || 24}px`
    const barH = 3
    const frag = document.createDocumentFragment()
    for (const level of vp.levels) {
      if (level.volume <= 0) continue
      const y = coord.priceToCoordinate(level.price)
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
      coord.createPriceLine({ price, color, lineWidth: 1, lineStyle, title, axisLabelVisible: true })
    profileLinesRef.current = [
      makeLine(vp.poc, VWAP, LineStyle.Solid, 'POC'),
      makeLine(vp.vah, 'rgba(148,163,184,0.7)', LineStyle.Dashed, 'VAH'),
      makeLine(vp.val, 'rgba(148,163,184,0.7)', LineStyle.Dashed, 'VAL'),
    ]
    profileOwnerRef.current = coord
  }, [])

  const scheduleProfile = useCallback(() => {
    if (profileRafRef.current) return
    profileRafRef.current = window.requestAnimationFrame(() => {
      profileRafRef.current = 0
      renderProfile()
    })
  }, [renderProfile])

  // Re-render the profile when the chart size changes (the histogram aligns to
  // the price scale). NOT on visible-range change — the profile range is the
  // fixed session, so zooming must not rebuild it (TradingView FRVP behaviour).
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
  }, [toggles.volumeProfile, scheduleProfile])

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
      // First paint of a dataset
      candleSeries.setData(candles.map(bar))
      volSeries.setData(candles.map(volBar))
      chart.timeScale().fitContent()
    } else if (last.time === prev.lastTime && candles.length === prev.n) {
      // In-progress bar updated in place (live mode) — O(1). The count guard
      // matters: a *replaced* dataset can share the last timestamp (e.g. 1m
      // vs Range both end at the final 1m candle) — in-place update would mix
      // the two series and corrupt the chart.
      candleSeries.update(bar(last))
      volSeries.update(volBar(last))
    } else if (candles.length > prev.n && candles[prev.n].time > prev.lastTime) {
      // Appended bars (replay tick at any speed) — update only the new tail,
      // keep the viewport stable. Avoids a full setData+fitContent per tick.
      // The time-continuation guard matters: an interval/contract switch can
      // produce a *larger* dataset whose times restart at the beginning, and
      // update() with an earlier time throws "Cannot update oldest data".
      for (let i = prev.n; i < candles.length; i++) {
        candleSeries.update(bar(candles[i]))
        volSeries.update(volBar(candles[i]))
      }
    } else {
      // Seek backward / dataset replaced — full rebuild + fit
      candleSeries.setData(candles.map(bar))
      volSeries.setData(candles.map(volBar))
      chart.timeScale().fitContent()
    }
    prevRef.current = { n: candles.length, lastTime: last.time }

    // Refresh the profile when its data source changes length. In replay the
    // source is the full window (context), which is stable — so a playing
    // replay doesn't rebuild the profile every tick. In live mode the source
    // is the candles, so a bar completion (length change) refreshes it.
    const ctx = contextDataRef.current
    const srcLen = ctx && ctx.length > 0 ? ctx.length : candles.length
    // Rebuild on dataset growth AND on dataset identity change (contract /
    // interval switch can keep the same bar count — e.g. NIFTY → BANKNIFTY —
    // which the length check alone would miss and leave a stale profile).
    const profileKey = `${symbol ?? ''}|${interval ?? ''}|${exchange ?? ''}|${root ?? ''}`
    if (profileKey !== profileKeyRef.current || srcLen !== profileLenRef.current) {
      profileKeyRef.current = profileKey
      profileLenRef.current = srcLen
      scheduleProfile()
    }
  }, [candles, scheduleProfile])

  return (
    <div className={`relative ${className ?? 'h-full w-full'}`} aria-label={ariaLabel}>
      {/* On-chart legend — overlays the top-left; toggles the same overlays
          the page used to expose as a separate row. */}
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
      {/* Volume-profile histogram — left edge, aligned to the price scale. */}
      <div
        ref={vpPanelRef}
        className="pointer-events-none absolute left-0 top-0 z-10 w-16 overflow-hidden"
        aria-hidden="true"
      />
    </div>
  )
}
