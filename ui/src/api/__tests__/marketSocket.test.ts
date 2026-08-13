import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketSocket } from '../client'

/** Minimal controllable WebSocket fake for protocol-level assertions. */
class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }
  send(data: string): void {
    this.sent.push(data)
  }
  close(): void {
    this.readyState = 3
    this.onclose?.()
  }
  // --- test helpers ---
  open(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
  receive(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) })
  }
  frames(): object[] {
    return this.sent.map((f) => JSON.parse(f))
  }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.useFakeTimers()
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('MarketSocket', () => {
  it('sends the correct subscribe wire shape ({type, symbol, exchange, interval})', () => {
    const s = new MarketSocket('ws://test/ws/market')
    s.connect()
    const ws = FakeWebSocket.instances[0]
    ws.open()
    s.subscribe('NIFTY AUG FUT', 'NFO', '1m')
    const frames = ws.frames()
    expect(frames).toContainEqual({
      type: 'subscribe',
      symbol: 'NIFTY AUG FUT',
      exchange: 'NFO',
      interval: '1m',
    })
    s.disconnect()
  })

  it('sends unsubscribe with the type field', () => {
    const s = new MarketSocket('ws://test/ws/market')
    s.connect()
    const ws = FakeWebSocket.instances[0]
    ws.open()
    s.subscribe('NIFTY AUG FUT', 'NFO', '1m')
    s.unsubscribe('NIFTY AUG FUT')
    expect(ws.frames()).toContainEqual({ type: 'unsubscribe', symbol: 'NIFTY AUG FUT' })
    s.disconnect()
  })

  it('resubscribes with the wire shape after reconnect', () => {
    const s = new MarketSocket('ws://test/ws/market')
    s.connect()
    const first = FakeWebSocket.instances[0]
    first.open()
    s.subscribe('NIFTY AUG FUT', 'NFO', '5m')

    // Simulate a drop + automatic reconnect (exponential backoff, jittered)
    first.close()
    expect(first.frames().some((f) => 'type' in f && f.type === 'subscribe')).toBe(true)
    vi.advanceTimersByTime(2500)

    const second = FakeWebSocket.instances[1]
    expect(second).toBeDefined()
    second.open()
    const resub = second.frames().filter((f) => (f as { type?: string }).type === 'subscribe')
    expect(resub).toEqual([{ type: 'subscribe', symbol: 'NIFTY AUG FUT', exchange: 'NFO', interval: '5m' }])
    s.disconnect()
  })

  it('forwards candle messages to listeners and stays silent on malformed frames', () => {
    const s = new MarketSocket('ws://test/ws/market')
    const got: unknown[] = []
    s.onMessage((m) => got.push(m))
    s.connect()
    const ws = FakeWebSocket.instances[0]
    ws.open()
    ws.receive({ type: 'candle', symbol: 'NIFTY AUG FUT', exchange: 'NFO', interval: '1m', candle: { time: 1, open: 1, high: 1, low: 1, close: 1, volume: 1 }, ts: 'x' })
    ws.onmessage?.({ data: '{not json' })
    expect(got).toHaveLength(1)
    s.disconnect()
  })
})
