import { useCallback, useEffect, useRef, useState } from 'react'
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
import { Eye, EyeOff, X } from 'lucide-react'
import { indicators, strategies } from '../lib/registry'
import type { ChartOverlays, StrategyPayload } from '../types/market'
import { isMcxSession } from '../lib/marketHours'
import type { Candle } from '../types/market'
import { fmtIST, IST_OFFSET_S, istChartTime } from '../lib/istTime'
import { fmtPrice, fmtVolume } from '../lib/format'
import type { ReplayPosition } from '../lib/replayPaperTrader'
import { calculateAdx } from '../lib/adx'

type CandleSeries = ISeriesApi<'Candlestick'>
type VolumeSeries = ISeriesApi<'Histogram'>
type LineSeries = ISeriesApi<'Line'>

export interface IndicatorToggles {
  vwap: boolean
  volumeProfile: boolean
  /** Strategy entries/exits (computed from the revealed bars). */
  strategy: boolean
  /** ADX (14) +DI / -DI Subplot */
  adx: boolean
}

interface ChartPanelProps {
  candles: Candle[]
  /** Strategy indicator overlays (default: all on). */
  indicators?: IndicatorToggles
  /** Toggle handler for the on-chart legend (delegated from the page). */
  onIndicators?: (key: keyof IndicatorToggles) => void
  /** Server-computed overlays (VWAP ±σ, volume profile, absorptions, ADX).
   *  Single source of truth — the FE never recomputes them. */
  overlays?: ChartOverlays | null
  /** Server-computed HalfTrend markers (signals + trend channels). */
  strategy?: StrategyPayload | null
  /** Active open replay position to render on-chart entry line, SL/TP and live floating P&L */
  replayPosition?: ReplayPosition | null
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
// TradingView HalfTrend palette: blue up / red down (matches the reference).
const HALFTREND_UP = '#2962FF'
const HALFTREND_DOWN = '#F23645'
const VWAP = '#F59E0B'
const VWAP_BAND = 'rgba(245, 158, 11, 0.35)'

// TradingView Volume Profile palette
const VP_POC = '#F23645' // Crimson Red (Point of Control)
const VP_VA = 'rgba(59, 130, 246, 0.75)' // High consensus 70% Value Area Blue
const VP_OUTSIDE = 'rgba(148, 163, 184, 0.25)' // Outside Value Area Muted Slate
const VP_LINE_VA = '#38BDF8' // Cyan for VAH & VAL levels

// ADX Subplot palette
const ADX_MAIN = '#F59E0B' // Amber
const ADX_PLUS_DI = '#26A69A' // Green/Teal
const ADX_MINUS_DI = '#EF5350' // Red/Crimson

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
 * ± σ bands, TradingView-standard volume profile (POC/VAH/VAL + Value Area histogram),
 * absorption markers, strategy signals, and dedicated ADX (14) +DI / -DI subplot.
 */
export function ChartPanel({
  candles,
  indicators: indicatorsProp,
  onIndicators,
  overlays,
  strategy,
  replayPosition,
  symbol,
  interval,
  exchange,
  root,
  className,
}: ChartPanelProps) {
  const toggles: IndicatorToggles = indicatorsProp ?? {
    vwap: false,
    volumeProfile: false,
    strategy: true,
    adx: false,
  }

  const [hoveredCandle, setHoveredCandle] = useState<Candle | null>(null)
  const [hoveredAdx, setHoveredAdx] = useState<{ adx?: number | null; plus_di?: number | null; minus_di?: number | null } | null>(null)

  const LEGEND: { key: keyof IndicatorToggles; label: string; color: string }[] = [
    { key: 'strategy', label: strategies['halftrend'].label, color: '#2962FF' },
    { key: 'vwap', label: indicators['vwap'].label, color: '#F59E0B' },
    { key: 'volumeProfile', label: indicators['volume_profile'].label, color: '#38BDF8' },
    { key: 'adx', label: 'ADX (14) Subplot', color: '#F59E0B' },
  ]

  const last = candles[candles.length - 1]
  const activeCandle = hoveredCandle ?? last
  const candleChange = activeCandle ? activeCandle.close - activeCandle.open : 0
  const candleChangePct =
    activeCandle && activeCandle.open > 0 ? (candleChange / activeCandle.open) * 100 : 0
  const isCandleUp = candleChange >= 0

  const ariaLabel = last
    ? `${symbol ?? ''} ${interval ?? ''} · O ${last.open} H ${last.high} L ${last.low} C ${last.close}`
    : `${symbol ?? ''} ${interval ?? ''} chart`

  const containerRef = useRef<HTMLDivElement | null>(null)
  const adxContainerRef = useRef<HTMLDivElement | null>(null)

  const chartRef = useRef<IChartApi | null>(null)
  const adxChartRef = useRef<IChartApi | null>(null)

  const candleRef = useRef<CandleSeries | null>(null)
  const volRef = useRef<VolumeSeries | null>(null)
  const vwapRef = useRef<LineSeries | null>(null)
  const vwapUpperRef = useRef<LineSeries | null>(null)
  const vwapLowerRef = useRef<LineSeries | null>(null)
  const htUpRef = useRef<LineSeries | null>(null)
  const htDownRef = useRef<LineSeries | null>(null)
  const atrHighRef = useRef<LineSeries | null>(null)
  const atrLowRef = useRef<LineSeries | null>(null)

  // ADX Subplot Series
  const adxMainSeriesRef = useRef<LineSeries | null>(null)
  const adxPlusSeriesRef = useRef<LineSeries | null>(null)
  const adxMinusSeriesRef = useRef<LineSeries | null>(null)

  const vpPanelRef = useRef<HTMLDivElement | null>(null)
  const prevRef = useRef<PrevRef | null>(null)
  const vwapPrevRef = useRef<{ n: number; lastTime: number } | null>(null)
  const absMarkersRef = useRef<string>('')
  const stratLinesRef = useRef<IPriceLine[]>([])
  const profileLinesRef = useRef<IPriceLine[]>([])
  const profileOwnerRef = useRef<CandleSeries | null>(null)
  const profileLenRef = useRef(-1)
  const profileRafRef = useRef(0)
  const scheduleProfileRef = useRef<(() => void) | null>(null)
  const isSyncingTimeScale = useRef(false)

  const candlesRef = useRef(candles)
  const indicatorsRef = useRef(toggles)
  const intervalRef = useRef(interval)
  const exchangeRef = useRef(exchange)
  const rootRef = useRef(root)
  const profileKeyRef = useRef('')
  const overlaysRef = useRef(overlays)
  const strategyRef = useRef(strategy)

  candlesRef.current = candles
  indicatorsRef.current = toggles
  intervalRef.current = interval
  exchangeRef.current = exchange
  rootRef.current = root
  overlaysRef.current = overlays
  strategyRef.current = strategy

  // --- render profile definition ------------------------------------
  const renderProfile = useCallback(() => {
    const chart = chartRef.current
    const cs = candleRef.current
    const panel = vpPanelRef.current
    if (!chart || !cs || !panel) return

    if (!indicatorsRef.current.volumeProfile) {
      const owner = profileOwnerRef.current
      if (owner) {
        for (const line of profileLinesRef.current) owner.removePriceLine(line)
      }
      profileOwnerRef.current = null
      profileLinesRef.current = []
      panel.innerHTML = ''
      return
    }

    const ov = overlaysRef.current
    const vp = ov?.volume_profile
    if (!vp || vp.levels.length === 0) {
      panel.innerHTML = ''
      return
    }

    const maxVol = Math.max(...vp.levels.map((l) => l.volume))
    const panelW = Math.min(220, Math.max(140, panel.clientWidth))
    panel.style.bottom = `${chart.timeScale().height() || 24}px`
    const barH = 3.5

    const frag = document.createDocumentFragment()
    for (const level of vp.levels) {
      if (level.volume <= 0) continue
      const y = cs.priceToCoordinate(level.price)
      if (y == null) continue
      if (y < -30 || y > panel.clientHeight + 30) continue

      const isPoc = Math.abs(level.price - vp.poc) < (vp.step / 2 || 0.05)
      const inVa = level.price >= vp.val - 0.001 && level.price <= vp.vah + 0.001
      const w = Math.max(3, (level.volume / maxVol) * panelW)

      const barEl = document.createElement('div')
      barEl.className = 'group transition-all duration-75 cursor-pointer'

      const barColor = isPoc ? VP_POC : inVa ? VP_VA : VP_OUTSIDE
      const barShadow = isPoc ? 'box-shadow: 0 0 6px rgba(242, 54, 69, 0.6);' : ''

      barEl.style.cssText =
        `position:absolute;left:0;top:${y - barH / 2}px;height:${barH}px;width:${w}px;` +
        `background:${barColor};border-radius:0 2px 2px 0;${barShadow}`

      barEl.title = `₹${level.price.toFixed(2)} · Vol: ${fmtVolume(level.volume)}${
        isPoc ? ' · POC (Point of Control)' : inVa ? ' · Inside 70% Value Area' : ' · Outside Value Area'
      }`

      frag.appendChild(barEl)
    }
    panel.innerHTML = ''
    panel.appendChild(frag)

    if (profileLinesRef.current.length === 0 || profileOwnerRef.current !== cs) {
      const makeLine = (
        price: number,
        color: string,
        lineWidth: 1 | 2,
        lineStyle: LineStyle,
        title: string,
      ): IPriceLine =>
        cs.createPriceLine({ price, color, lineWidth, lineStyle, title, axisLabelVisible: true })

      profileLinesRef.current = [
        makeLine(vp.poc, VP_POC, 2, LineStyle.Solid, 'POC'),
        makeLine(vp.vah, VP_LINE_VA, 1, LineStyle.Dashed, 'VAH'),
        makeLine(vp.val, VP_LINE_VA, 1, LineStyle.Dashed, 'VAL'),
      ]
      profileOwnerRef.current = cs
    }
  }, [])

  const scheduleProfile = useCallback(() => {
    if (profileRafRef.current) return
    profileRafRef.current = window.requestAnimationFrame(() => {
      profileRafRef.current = 0
      renderProfile()
    })
  }, [renderProfile])

  scheduleProfileRef.current = scheduleProfile

  // --- Main Chart initialization ------------------------------------
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
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#475569', labelBackgroundColor: '#1E293B' },
        horzLine: { color: '#475569', labelBackgroundColor: '#1E293B' },
      },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
        visible: !toggles.adx, // if ADX subplot is open, ADX subplot shows the bottom time axis
      },
      localization: {
        timeFormatter: (time: UTCTimestamp) =>
          typeof time === 'number' ? fmtIST(time - IST_OFFSET_S, { year: true }) : String(time),
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
    })

    const candlestickSeries = chart.addCandlestickSeries({
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

    const htLineOpts = {
      lineWidth: 2 as const,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    }
    const htUp = chart.addLineSeries({ ...htLineOpts, color: HALFTREND_UP })
    const htDown = chart.addLineSeries({ ...htLineOpts, color: HALFTREND_DOWN })

    const atrOpts = {
      color: 'rgba(148, 163, 184, 0.55)',
      lineWidth: 1 as const,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    }
    const atrHigh = chart.addLineSeries(atrOpts)
    const atrLow = chart.addLineSeries(atrOpts)

    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })

    const onRangeMove = () => {
      scheduleProfileRef.current?.()
      if (adxChartRef.current && !isSyncingTimeScale.current) {
        const range = chart.timeScale().getVisibleLogicalRange()
        if (range) {
          isSyncingTimeScale.current = true
          adxChartRef.current.timeScale().setVisibleLogicalRange(range)
          isSyncingTimeScale.current = false
        }
      }
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeMove)
    chart.timeScale().subscribeVisibleTimeRangeChange(onRangeMove)

    chart.subscribeCrosshairMove((param) => {
      scheduleProfileRef.current?.()
      if (!param.time || !param.seriesData) {
        setHoveredCandle(null)
        return
      }
      const cData = param.seriesData.get(candlestickSeries) as
        | { open: number; high: number; low: number; close: number }
        | undefined
      const vData = param.seriesData.get(volume) as { value: number } | undefined
      if (cData) {
        setHoveredCandle({
          time: typeof param.time === 'number' ? param.time : 0,
          open: cData.open,
          high: cData.high,
          low: cData.low,
          close: cData.close,
          volume: vData?.value ?? 0,
        })
      } else {
        setHoveredCandle(null)
      }
    })

    const handlePointerAction = () => {
      scheduleProfileRef.current?.()
    }
    el.addEventListener('pointermove', handlePointerAction)
    el.addEventListener('mousemove', handlePointerAction)
    el.addEventListener('wheel', handlePointerAction, { passive: true })
    el.addEventListener('pointerup', handlePointerAction)
    el.addEventListener('mouseup', handlePointerAction)

    chartRef.current = chart
    candleRef.current = candlestickSeries
    volRef.current = volume
    vwapRef.current = vwap
    vwapUpperRef.current = vwapUpper
    vwapLowerRef.current = vwapLower
    htUpRef.current = htUp
    htDownRef.current = htDown
    atrHighRef.current = atrHigh
    atrLowRef.current = atrLow

    return () => {
      el.removeEventListener('pointermove', handlePointerAction)
      el.removeEventListener('mousemove', handlePointerAction)
      el.removeEventListener('wheel', handlePointerAction)
      el.removeEventListener('pointerup', handlePointerAction)
      el.removeEventListener('mouseup', handlePointerAction)
      if (profileRafRef.current) window.cancelAnimationFrame(profileRafRef.current)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volRef.current = null
      vwapRef.current = null
      vwapUpperRef.current = null
      vwapLowerRef.current = null
      htUpRef.current = null
      htDownRef.current = null
      atrHighRef.current = null
      atrLowRef.current = null
      prevRef.current = null
      vwapPrevRef.current = null
      stratLinesRef.current = []
      profileLinesRef.current = []
      profileOwnerRef.current = null
    }
  }, [toggles.adx])

  // --- ADX Subplot Chart initialization ------------------------------
  useEffect(() => {
    if (!toggles.adx) {
      if (adxChartRef.current) {
        adxChartRef.current.remove()
        adxChartRef.current = null
        adxMainSeriesRef.current = null
        adxPlusSeriesRef.current = null
        adxMinusSeriesRef.current = null
      }
      return
    }

    const el = adxContainerRef.current
    if (!el) return

    const adxChart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#0F172A' },
        textColor: '#94A3B8',
        fontSize: 10,
        fontFamily: "'JetBrains Mono', ui-monospace, Menlo, monospace",
      },
      grid: {
        vertLines: { color: 'rgba(51, 65, 85, 0.25)' },
        horzLines: { color: 'rgba(51, 65, 85, 0.25)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#475569', labelBackgroundColor: '#1E293B' },
        horzLine: { color: '#475569', labelBackgroundColor: '#1E293B' },
      },
      rightPriceScale: {
        borderColor: '#334155',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
      localization: {
        timeFormatter: (time: UTCTimestamp) =>
          typeof time === 'number' ? fmtIST(time - IST_OFFSET_S, { year: true }) : String(time),
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
    })

    const adxSeries = adxChart.addLineSeries({
      color: ADX_MAIN,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'ADX',
    })

    const plusSeries = adxChart.addLineSeries({
      color: ADX_PLUS_DI,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      title: '+DI',
    })

    const minusSeries = adxChart.addLineSeries({
      color: ADX_MINUS_DI,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      title: '-DI',
    })

    // Baseline reference lines: 20 (Ranging/Trending boundary) and 25 (Strong trend)
    adxSeries.createPriceLine({
      price: 20,
      color: '#64748B',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: '20',
      axisLabelVisible: true,
    })
    adxSeries.createPriceLine({
      price: 25,
      color: '#94A3B8',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: '25 Strong',
      axisLabelVisible: true,
    })

    // Synchronize ADX scroll -> Main chart
    adxChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (chartRef.current && range && !isSyncingTimeScale.current) {
        isSyncingTimeScale.current = true
        chartRef.current.timeScale().setVisibleLogicalRange(range)
        isSyncingTimeScale.current = false
      }
    })

    // Initial logical range sync from main chart
    if (chartRef.current) {
      const currentRange = chartRef.current.timeScale().getVisibleLogicalRange()
      if (currentRange) adxChart.timeScale().setVisibleLogicalRange(currentRange)
    }

    adxChart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) {
        setHoveredAdx(null)
        return
      }
      const adxVal = param.seriesData.get(adxSeries) as { value?: number } | undefined
      const plusVal = param.seriesData.get(plusSeries) as { value?: number } | undefined
      const minusVal = param.seriesData.get(minusSeries) as { value?: number } | undefined
      setHoveredAdx({
        adx: adxVal?.value ?? null,
        plus_di: plusVal?.value ?? null,
        minus_di: minusVal?.value ?? null,
      })
    })

    adxChartRef.current = adxChart
    adxMainSeriesRef.current = adxSeries
    adxPlusSeriesRef.current = plusSeries
    adxMinusSeriesRef.current = minusSeries

    return () => {
      adxChart.remove()
      adxChartRef.current = null
      adxMainSeriesRef.current = null
      adxPlusSeriesRef.current = null
      adxMinusSeriesRef.current = null
      setHoveredAdx(null)
    }
  }, [toggles.adx])

  // --- ADX Subplot Data Feed -----------------------------------------
  useEffect(() => {
    if (!toggles.adx) return
    const adxS = adxMainSeriesRef.current
    const plusS = adxPlusSeriesRef.current
    const minusS = adxMinusSeriesRef.current
    if (!adxS || !plusS || !minusS) return

    if (!candles || candles.length === 0) {
      adxS.setData([])
      plusS.setData([])
      minusS.setData([])
      return
    }

    const serverAdx = overlaysRef.current?.adx?.series
    const adxData = serverAdx && serverAdx.length > 0 ? serverAdx : calculateAdx(candles, 14)
    if (!adxData || adxData.length === 0) {
      adxS.setData([])
      plusS.setData([])
      minusS.setData([])
      return
    }

    // Restrict ADX data to revealed candles up to current replay/live time
    const lastTime = candles.length > 0 ? candles[candles.length - 1].time : -1

    const adxPts: { time: UTCTimestamp; value?: number }[] = []
    const plusPts: { time: UTCTimestamp; value?: number }[] = []
    const minusPts: { time: UTCTimestamp; value?: number }[] = []

    for (const p of adxData) {
      if (lastTime > 0 && p.time > lastTime) continue
      const t = istChartTime(p.time) as UTCTimestamp
      if (p.adx != null) adxPts.push({ time: t, value: p.adx })
      else adxPts.push({ time: t })

      if (p.plus_di != null) plusPts.push({ time: t, value: p.plus_di })
      else plusPts.push({ time: t })

      if (p.minus_di != null) minusPts.push({ time: t, value: p.minus_di })
      else minusPts.push({ time: t })
    }

    adxS.setData(adxPts)
    plusS.setData(plusPts)
    minusS.setData(minusPts)
  }, [overlays, toggles.adx, candles])

  // --- server overlays (VWAP, Bands) ---------------------------------
  useEffect(() => {
    const v = vwapRef.current
    const u = vwapUpperRef.current
    const l = vwapLowerRef.current
    if (!v || !u || !l) return
    const ov = overlaysRef.current
    if (!toggles.vwap || !ov || !ov.vwap) {
      v.setData([])
      u.setData([])
      l.setData([])
      vwapPrevRef.current = null
      return
    }
    const series = ov.vwap
    const last = series[series.length - 1]
    const lastTime = last ? last.time : -1
    const prev = vwapPrevRef.current

    const bandValueAt = (p: { time: number; value: number | null } | undefined): number | null => {
      if (!p || p.value == null) return null
      return p.value
    }
    const upperConst = bandValueAt(ov.vwap_upper?.[ov.vwap_upper.length - 1])
    const lowerConst = bandValueAt(ov.vwap_lower?.[ov.vwap_lower.length - 1])
    const firstValid = series.findIndex((p) => p.value != null)
    const bandSpan = firstValid >= 0 ? series.slice(firstValid) : []
    const uppers =
      upperConst != null && bandSpan.length > 0
        ? bandSpan.map((p) => ({ time: p.time, value: upperConst }))
        : []
    const lowers =
      lowerConst != null && bandSpan.length > 0
        ? bandSpan.map((p) => ({ time: p.time, value: lowerConst }))
        : []

    const vwapData = series.map((p) =>
      p.value == null
        ? { time: istChartTime(p.time) as UTCTimestamp }
        : { time: istChartTime(p.time) as UTCTimestamp, value: p.value as number },
    )
    if (prev && series.length > 0 && series.length === prev.n && lastTime === prev.lastTime) {
      if (last.value != null)
        v.update({ time: istChartTime(last.time) as UTCTimestamp, value: last.value as number })
      if (uppers.length) v.update(shift(uppers[uppers.length - 1]))
      if (lowers.length) v.update(shift(lowers[lowers.length - 1]))
    } else {
      v.setData(vwapData)
      u.setData(uppers.map(shift))
      l.setData(lowers.map(shift))
    }
    vwapPrevRef.current = { n: series.length, lastTime }
  }, [overlays, toggles.vwap])

  // HalfTrend markers & channels
  useEffect(() => {
    const cs = candleRef.current
    const htUp = htUpRef.current
    const htDown = htDownRef.current
    const atrH = atrHighRef.current
    const atrL = atrLowRef.current
    if (!cs || !htUp || !htDown || !atrH || !atrL) return
    const src = candlesRef.current
    const strat = strategyRef.current
    const isHt = (strat?.kind ?? strat?.id) === 'halftrend'
    const show = toggles.strategy && isHt

    const timeOf = (t: number) => {
      const idx = src.findIndex((c) => c.time === t)
      if (idx >= 0) return istChartTime(src[idx].time) as UTCTimestamp
      return istChartTime(t) as UTCTimestamp
    }
    const markers: SeriesMarker<UTCTimestamp>[] = []
    if (show && strat?.markers) {
      const lastTime = src.length > 0 ? src[src.length - 1].time : -1
      for (const m of strat.markers) {
        if (m.time > lastTime) continue
        markers.push({
          time: timeOf(m.time),
          position: m.side === 'BUY' ? 'belowBar' : 'aboveBar',
          shape: m.side === 'BUY' ? 'arrowUp' : 'arrowDown',
          color: m.side === 'BUY' ? HALFTREND_UP : HALFTREND_DOWN,
        })
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    const key = JSON.stringify(markers)
    if (key !== absMarkersRef.current) {
      absMarkersRef.current = key
      cs.setMarkers(markers)
    }

    const series = show ? strat?.series : null
    const n = series ? Math.min(src.length, series.ht.length, series.trend.length) : 0
    if (!series || n === 0) {
      htUp.setData([])
      htDown.setData([])
      atrH.setData([])
      atrL.setData([])
      return
    }
    type Pt = { time: UTCTimestamp; value?: number }
    const up: Pt[] = []
    const down: Pt[] = []
    const high: Pt[] = []
    const low: Pt[] = []
    for (let i = 0; i < n; i++) {
      const time = istChartTime(src[i].time) as UTCTimestamp
      const ht = series.ht[i]
      const tr = series.trend[i]
      if (ht == null) {
        up.push({ time })
        down.push({ time })
      } else if (tr === 0) {
        up.push({ time, value: ht })
        down.push({ time })
      } else {
        down.push({ time, value: ht })
        up.push({ time })
      }
      const ah = series.atrHigh[i]
      const al = series.atrLow[i]
      high.push(ah == null ? { time } : { time, value: ah })
      low.push(al == null ? { time } : { time, value: al })
    }
    htUp.setData(up)
    htDown.setData(down)
    atrH.setData(high)
    atrL.setData(low)
  }, [strategy, toggles.strategy, candles])

  // On-chart position entry, SL, and TP price lines
  useEffect(() => {
    const cs = candleRef.current
    if (!cs) return
    for (const line of stratLinesRef.current) cs.removePriceLine(line)
    stratLinesRef.current = []

    if (replayPosition != null) {
      const isWin = replayPosition.unrealized_pnl >= 0
      stratLinesRef.current.push(
        cs.createPriceLine({
          price: replayPosition.entry_price,
          color: isWin ? '#26A69A' : '#EF5350',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          title: `${replayPosition.side} ${replayPosition.qty} @ ${fmtPrice(
            replayPosition.entry_price,
          )} (${isWin ? '+' : ''}₹${replayPosition.unrealized_pnl.toFixed(2)})`,
          axisLabelVisible: true,
        }),
      )
      if (replayPosition.sl != null) {
        stratLinesRef.current.push(
          cs.createPriceLine({
            price: replayPosition.sl,
            color: '#EF5350',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: `SL: ${fmtPrice(replayPosition.sl)}`,
            axisLabelVisible: true,
          }),
        )
      }
      if (replayPosition.tp != null) {
        stratLinesRef.current.push(
          cs.createPriceLine({
            price: replayPosition.tp,
            color: '#26A69A',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: `TP: ${fmtPrice(replayPosition.tp)}`,
            axisLabelVisible: true,
          }),
        )
      }
      return
    }

    if (!toggles.strategy || !strategy) return
    const sigs = strategy.signals
    if (!sigs) return
    const entries = sigs.filter((s) => !s.exit_reason)
    const open = entries[entries.length - 1]
    if (!open) return
    if (open.sl != null) {
      stratLinesRef.current.push(
        cs.createPriceLine({
          price: open.sl,
          color: '#EF5350',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: 'SL',
          axisLabelVisible: true,
        }),
      )
    }
    if (open.tp != null) {
      stratLinesRef.current.push(
        cs.createPriceLine({
          price: open.tp,
          color: '#26A69A',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: 'TP',
          axisLabelVisible: true,
        }),
      )
    }
  }, [strategy, toggles.strategy, replayPosition])

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

    const profileKey = `${symbol ?? ''}|${interval ?? ''}|${exchange ?? ''}|${root ?? ''}`
    if (profileKey !== profileKeyRef.current || candles.length !== profileLenRef.current) {
      profileKeyRef.current = profileKey
      profileLenRef.current = candles.length
      scheduleProfile()
    }
  }, [candles, scheduleProfile, symbol, interval, exchange, root])

  const vpData = overlays?.volume_profile

  // Compute latest ADX values for the subplot header
  const adxSeriesData =
    overlays?.adx?.series && overlays.adx.series.length > 0
      ? overlays.adx.series
      : calculateAdx(candles, 14)
  const latestAdxPoint =
    adxSeriesData.length > 0 ? adxSeriesData[adxSeriesData.length - 1] : null
  const currentAdxVal = hoveredAdx?.adx ?? latestAdxPoint?.adx ?? null
  const currentPlusDi = hoveredAdx?.plus_di ?? latestAdxPoint?.plus_di ?? null
  const currentMinusDi = hoveredAdx?.minus_di ?? latestAdxPoint?.minus_di ?? null
  const isTrendStrong = currentAdxVal != null && currentAdxVal >= 25

  return (
    <div className={`flex flex-col ${className ?? 'h-full w-full'}`} aria-label={ariaLabel}>
      {/* Main Candlestick Chart Area */}
      <div className="relative flex-1 min-h-0 w-full overflow-hidden">
        {/* TradingView Top-Left Floating Header: Symbol, Timeframe, Live OHLCV, and Indicator Badges */}
        <div className="pointer-events-auto absolute left-3 top-3 z-20 flex flex-col gap-1.5 font-mono">
          {/* Symbol + OHLCV Row */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-[#0F172A]/85 backdrop-blur-md px-2.5 py-1.5 text-xs shadow-md border border-line/50">
            <span className="font-bold text-ink">{symbol || '—'}</span>
            <span className="rounded bg-panel2 px-1 py-0.2 text-[10px] text-muted">{interval}</span>

            {activeCandle && (
              <div className="flex items-center gap-2.5 text-[11px]">
                <span>O <b className="text-ink">{fmtPrice(activeCandle.open)}</b></span>
                <span>H <b className="text-ink">{fmtPrice(activeCandle.high)}</b></span>
                <span>L <b className="text-ink">{fmtPrice(activeCandle.low)}</b></span>
                <span>C <b className="text-ink">{fmtPrice(activeCandle.close)}</b></span>
                <span>
                  <b className={isCandleUp ? 'text-bull' : 'text-bear'}>
                    {isCandleUp ? '+' : ''}{candleChange.toFixed(2)} ({isCandleUp ? '+' : ''}{candleChangePct.toFixed(2)}%)
                  </b>
                </span>
                <span>Vol <b className="text-ink">{fmtVolume(activeCandle.volume)}</b></span>
              </div>
            )}
          </div>

          {/* Replay Active Position & Live PnL Ribbon */}
          {replayPosition && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-[#0F172A]/90 backdrop-blur-md px-2.5 py-1 text-xs shadow-md border border-line/60">
              <span
                className={`h-2 w-2 rounded-full ${
                  replayPosition.unrealized_pnl >= 0 ? 'bg-accent animate-pulse' : 'bg-danger animate-pulse'
                }`}
              />
              <span
                className={`font-bold ${
                  replayPosition.side === 'BUY' ? 'text-accent' : 'text-danger'
                }`}
              >
                {replayPosition.side} {replayPosition.qty} @ ₹{fmtPrice(replayPosition.entry_price)}
              </span>
              <span>·</span>
              <span className="text-muted text-[11px]">Live Profit:</span>
              <span
                className={`font-bold ${
                  replayPosition.unrealized_pnl >= 0 ? 'text-accent' : 'text-danger'
                }`}
              >
                {replayPosition.unrealized_pnl >= 0 ? '+' : ''}₹
                {replayPosition.unrealized_pnl.toFixed(2)} (
                {replayPosition.return_pct >= 0 ? '+' : ''}
                {replayPosition.return_pct.toFixed(2)}%)
              </span>
              {replayPosition.sl != null && (
                <span className="text-[10px] text-danger hidden md:inline">
                  SL: ₹{fmtPrice(replayPosition.sl)}
                </span>
              )}
              {replayPosition.tp != null && (
                <span className="text-[10px] text-accent hidden md:inline">
                  TP: ₹{fmtPrice(replayPosition.tp)}
                </span>
              )}
            </div>
          )}

          {/* Overlay Indicator Chips with Eye Toggle Buttons */}
          {onIndicators && (
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              {LEGEND.map(({ key, label, color }) => {
                const active = toggles[key]
                return (
                  <div
                    key={key}
                    className={`flex items-center gap-1.5 rounded-md border px-2 py-0.5 backdrop-blur-md transition-all ${
                      active
                        ? 'border-line/70 bg-[#0F172A]/85 text-ink'
                        : 'border-line/30 bg-[#0F172A]/40 text-muted/50'
                    }`}
                  >
                    <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                    <span className="text-[10px] font-semibold">{label}</span>
                    <button
                      type="button"
                      onClick={() => onIndicators(key)}
                      className="p-0.5 text-muted hover:text-ink"
                      title={active ? `Hide ${label}` : `Show ${label}`}
                    >
                      {active ? <Eye className="h-3 w-3 text-accent" /> : <EyeOff className="h-3 w-3" />}
                    </button>
                  </div>
                )
              })}
              {toggles.volumeProfile && vpData && (
                <span className="rounded bg-panel2/90 px-2 py-0.5 text-[10px] font-mono text-muted border border-line/60 hidden sm:inline-flex items-center gap-2">
                  <span>
                    Sess VP <b className="text-muted/80">{isMcxSession(exchange, root) ? '09:00' : '09:15'}</b>
                  </span>
                  <span>·</span>
                  <span>POC <b className="text-[#F23645] font-bold">{vpData.poc.toFixed(2)}</b></span>
                  <span>VAH <b className="text-[#38BDF8]">{vpData.vah.toFixed(2)}</b></span>
                  <span>VAL <b className="text-[#38BDF8]">{vpData.val.toFixed(2)}</b></span>
                  <span className="text-[9px] text-muted/70">(70% VA)</span>
                </span>
              )}
            </div>
          )}
        </div>

        {/* Main Chart Canvas */}
        <div ref={containerRef} className="h-full w-full" />

        {/* Volume Profile Overlay Container */}
        <div
          ref={vpPanelRef}
          className="pointer-events-auto absolute left-0 top-0 z-10 w-52 sm:w-60 overflow-hidden"
          aria-hidden="true"
        />
      </div>

      {/* ADX Subplot Pane */}
      {toggles.adx && (
        <div className="relative h-[130px] w-full min-h-[110px] border-t border-line/70 bg-[#0F172A]">
          {/* Subplot Header */}
          <div className="pointer-events-auto absolute left-3 top-2 z-20 flex items-center gap-2 rounded bg-[#0F172A]/90 px-2 py-0.5 text-[11px] font-mono shadow-sm border border-line/40">
            <span className="font-bold text-ink">ADX (14)</span>
            <div className="flex items-center gap-2">
              <span className="text-[#F59E0B] font-semibold">
                ADX: <b>{currentAdxVal != null ? currentAdxVal.toFixed(1) : '—'}</b>
              </span>
              <span className="text-[#26A69A] font-semibold">
                +DI: <b>{currentPlusDi != null ? currentPlusDi.toFixed(1) : '—'}</b>
              </span>
              <span className="text-[#EF5350] font-semibold">
                -DI: <b>{currentMinusDi != null ? currentMinusDi.toFixed(1) : '—'}</b>
              </span>
              {currentAdxVal != null && (
                <span
                  className={`rounded px-1 text-[9px] font-bold ${
                    isTrendStrong
                      ? 'bg-accent/20 text-accent'
                      : 'bg-panel2 text-muted'
                  }`}
                >
                  {isTrendStrong ? 'STRONG TREND (≥25)' : 'WEAK / RANGING'}
                </span>
              )}
            </div>
            {onIndicators && (
              <button
                type="button"
                onClick={() => onIndicators('adx')}
                className="ml-1 p-0.5 text-muted hover:text-ink"
                title="Close ADX Subplot"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          {/* ADX Canvas */}
          <div ref={adxContainerRef} className="h-full w-full" />
        </div>
      )}
    </div>
  )
}
