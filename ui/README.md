# nTrade Futures Terminal (UI)

A futures symbol-selection + chart-replay trading screen for nTrade: pick a
root symbol (NIFTY / BANKNIFTY / …), choose a futures expiry contract, view
OHLCV candles on a TradingView chart, then replay history as if candles were
arriving in real time.

```
ui/            React + Vite + TypeScript frontend (this directory)
api/           FastAPI backend that serves both the API and this built UI
ntrade/        existing framework (untouched) — brokers, sim, domain objects
```

## Stack

React 18 · Vite 5 · TypeScript · Tailwind CSS · Zustand · TradingView
**lightweight-charts** v4 · Lucide icons. Backend: FastAPI + uvicorn
(`[project.optional-dependencies] ui`).

## Run

```bash
# 1. Backend (serves the built UI at http://127.0.0.1:8000)
./.venv/bin/python -m api --provider synthetic   # dhan | parquet also supported
#    Historical data only by default. Add --live-stream to re-enable the
#    (synthetic) tick pump, e.g. for demoing Live mode during market hours.

# 2. Frontend dev (HMR, proxies /api + /ws to :8000) — optional
cd ui && npm install && npm run dev              # http://localhost:5173

# 3. Production build of the UI (needed once before step 1 serves the page)
cd ui && npm run build                           # → ui/dist
```

`--provider` selects the market-data backend:

| Provider | Source | When to use |
|---|---|---|
| `synthetic` (default) | deterministic seeded OHLCV, offline | local dev, tests, replay demos |
| `dhan` | live DhanBroker (real candles/quotes) | live trading data (needs `.env` creds) |
| `parquet` | offline `data/ohlcv/` parquet store | symbols already backfilled |

**Datalake write-through:** with `dhan`, every futures-candle fetch for
NIFTY/BANKNIFTY is also persisted into `data/ohlcv/` (same Hive-partitioned
Parquet store `parquet` reads, timestamps stored as naive IST wall time).
Best-effort — a storage failure never fails the request. `NTRADE_DATA_DIR`
overrides the store root. Offline, if the store lacks `1D` rows the parquet
quote derives the daily bar from the stored intraday candles, so the header
quote works from the datalake alone.

## API endpoints & data contracts

All endpoints are under `/api/market` (broker-agnostic — see `api/marketdata.py`).

| Endpoint | Returns |
|---|---|
| `GET /api/market/provider` | active provider + instrument-master status |
| `GET /api/market/roots` | futures roots — index (`NIFTY`/`BANKNIFTY`) then MCX (`CRUDEOIL`/`GOLD`/…) — with front-month contract |
| `GET /api/market/roots/{root}/contracts` | futures contracts: `contract_id`, `symbol`, `expiry`, `lot_size`, `tick_size`, `is_front_month`, `is_expired` |
| `GET /api/market/candles?symbol=&interval=1m\|5m\|15m\|1h\|1D&start=&end=&limit=` | normalized candles |
| `GET /api/market/ticks?symbol=&interval=&start=&end=&limit=` | synthesized 1-second replay ticks, compact per-bar form (`bars: [{time, prices[], quantities[]}]`; bar-faithful: O/H/L/C anchored, volume distributed) |
| `GET /api/market/quote?symbol=` | ltp, change %, day OHLC, volume, source |
| `WS /ws/market` | `subscribe`/`unsubscribe` → live `candle` + `live_status` messages |

**Candle DTO** (the only shape the UI consumes):

```ts
{ time: number, open: number, high: number, low: number, close: number, volume: number }
// time = UTC epoch seconds; naive input timestamps are interpreted as IST
```

**Timezone convention:** the wire is UTC epoch seconds end-to-end (unambiguous,
backend-agnostic). The UI renders **everything in IST** (`Asia/Kolkata`),
independent of the browser's timezone: the chart axis/crosshair show IST wall-
clock (`src/lib/istTime.ts` — series times are shifted +05:30 because
lightweight-charts v4 draws the axis in UTC, and the crosshair formatter
reverses the shift), and all textual readouts use `timeZone: 'Asia/Kolkata'`
explicitly. So an 09:15 IST bar shows as `09:15` on any machine.

Instrument metadata (roots, contracts, expiries) comes from Dhan's real
instrument-master dumps (`Dependencies\all_instrument *.csv` — parsed by
`FuturesMaster`), so the expiry/contract list is production data even in
offline mode.

## Replay design

Replay is 100% client-side over already-fetched historical candles, and by
**default animates each bar's intra-bar path at 1-second tick level** so it
plays back like a real market feed:

- `GET /api/market/ticks` synthesizes deterministic 1-second ticks per bar by
  reusing the framework's `ntrade.sim.tick_simulator.synthesize_1m_ticks` —
  open/close anchored, high/low genuinely touched, bar volume distributed
  across ticks, seeded per (symbol, bar) so replays are reproducible.
- `src/hooks/replayReducer.ts` — a **pure** state machine
  (`idle → playing → paused → completed`); cursor/total are in *tick* space
  (`stepsPerBar` = 60 ticks per 1m bar, 1 = plain bar replay), speed 1×–60×,
  seek/reset/jump-to-latest. Unit-tested in
  `src/hooks/__tests__/replayReducer.test.ts`.
- `src/hooks/replayVisible.ts` — pure helpers that build the revealed candle
  set from the compact per-bar ticks, animating the in-progress bar from the
  ticks revealed so far; bars beyond the tick budget render as their real
  candles. Unit-tested in
  `src/hooks/__tests__/replayVisible.test.ts`.
- `useReplay` runs a `setInterval` only while `status === 'playing'`, torn
  down on pause/unmount/speed change — no timer leaks. 1× = one 1-second tick
  per second (real-time pace).
- `ChartPanel` uses `series.update()` for in-place bar updates (O(1) — smooth
  tick playback) and a full `setData()` + `fitContent()` only on
  seek/contract/interval changes.
- Replay vs Live is an explicit mode toggle; the header/status badges and a
  `simulated 1s ticks` note make the current mode unmistakable. If tick data
  is unavailable (network error, range over the `MAX_TICKS` budget) replay
  falls back to plain bar-by-bar playback automatically.
- **Replay window** — a From/To pair of IST datetime inputs in the replay bar
  confines playback to a slice of history (`windowBars` in `replayVisible.ts`
  slices the candles upstream; the reducer/chart/controls then see the slice
  as their dataset). **Defaults to the last 3 trading days** (`lastNDays` —
  dates grouped by IST wall date so a 00:00 IST bar isn't misdated to the
  previous UTC day); editing From/To overrides it, and playback starts at
  From, completes at To, with slider/readouts window-relative. Inputs are
  IST-deterministic (`fmtISTInput`/`istInputToEpoch` in `src/lib/istTime.ts`)
  and clamp to the dataset bounds.
- **TradingView-style context** — during replay the chart renders the whole
  window **dimmed** behind the revealed bars (`context` prop → a second,
  grayed-out candlestick + volume series in `ChartPanel`), so you see the
  full history at once; bars ahead of the playback position stay ghosted and
  turn full-color as the cursor reaches them.

**Live mode** streams via `/ws/market` (`MarketSocket` with exponential-backoff
reconnect + auto-resubscribe). Incoming candles merge into the historical set
(live wins by `time`); the in-progress bar updates in place.

Live streaming is on by default, gated by exchange hours. `--provider dhan`
forms in-progress candles from Dhan's MarketFeed websocket (real LTP/LTQ).
`synthetic` / `parquet` keep the 1Hz demo walk. Pass `--no-live-stream` for
historical-only.

## Range bars

The interval selector has a **Range** option (after a divider, next to the
5 timeframes): price-based bars derived client-side from the loaded 1m feed
(`src/lib/rangeBars.ts` — a TS mirror of
`ntrade/domain/analytics/range_bars.py`, the primitive the Valentini
scalper actually trades on). A bar closes when price travels `rangeSize`
(`high - low >= rangeSize`) regardless of how many 1m candles that took,
traversed along the canonical O→H→L→C / O→L→H→C path with volume
distributed proportionally. The range size is auto-sized from ATR(14)
rounded to the contract's tick grid (`contract.tick_size`) — or set
manually with the **Range Size** control that appears next to the selector:
`Auto` (ATR) or a number of ticks (e.g. `10` ticks × `tick_size`). Changing
it rebuilds the bars client-side from the already-loaded 1m feed (no server
refetch). The trailing partial bar is marked incomplete. `Range` is never
sent to the wire — the backend keeps serving timeframes — so live WS
updates and the synthesized 1s tick replay are skipped for it (replay
falls back to plain bar playback). The indicator overlays (VWAP, volume
profile, absorptions) work on range bars too.

## Indicators (Valentini / Fabio overlays)

The chart renders the strategy's signal layer client-side — pure TS mirrors of
`ntrade/domain/analytics/` (`src/lib/indicators.ts`, unit-tested), computed
from the candles already loaded, so no server round-trip:

- **VWAP + bands** — per-IST-session VWAP (fresh accumulation each trading day,
  the standard intraday VWAP) with ±2σ volume-weighted bands, drawn as an
  amber line + dashed translucent bands. During replay it builds from the
  revealed bars only.
- **Volume Profile** — POC / VAH / VAL (68% value area): an amber POC price
  line + dashed VAH/VAL lines, plus a horizontal histogram on the chart's
  left edge (aligned via the candle series' `priceToCoordinate`, rebuilt on
  scroll/zoom/resize). In **replay** it is computed over the **full replay
  window** (the dimmed context series) so it is stable while the cursor
  advances — only the visible-logical-range slice narrows it when zooming.
  Live mode computes it over the candles. Parameter defaults match the
  strategy (`VALUE_AREA_PCT` 0.68, bucket = 1/50th of the span).
- **Absorption** — "big volume, no price" bars (the strategy's phase-1
  signal) as green below-bar arrows (BUY) / red above-bar arrows (SELL),
  using the same defaults as `detect_absorptions` (1.5× volume, 0.5× range).
- **Strategy** — the full Valentini scalper state machine
  (`src/lib/valentini.ts`, a TS mirror of `ValentiniScalper` in
  `ntrade/engines/strategies.py`): absorption → accumulation → aggression
  entry with SL/TP, then managed exits (stop / target / 0.5R breakeven
  trail / hard session close at 15:25 IST). Entries draw **circles** (BUY
  below-bar teal / SELL above-bar red) labelled `BUY`/`SELL`, exits
  **squares** colored and labelled by reason (`TP` teal / `SL` red / `END`
  amber), and an open trade's SL/TP show as dashed price lines. The state
  machine runs on the **completed** revealed bars only (the in-progress bar
  is excluded — the strategy reacts to candle closes), keyed on the
  completed-bar count so it recomputes once per bar completion, never per
  intra-bar tick.

  **Strictly intraday** — the machine carries no overnight state, so replay
  over a multi-day window stays accurate: absorptions are detected per IST
  trading session (yesterday's volume/range context can't bleed into
  today's early bars), an open trade is force-closed at the prior day's
  close even when the data jumps straight from 15:25 to the next 09:15, and
  the phase resets to Waiting at each day boundary so yesterday's setup can
  never arm today's entry.

A small **Indicators** toggle row above the chart hides/shows each overlay.
A **Fabio** strip under the chart (replay mode) shows the current phase
(Waiting / Absorbing / Accumulating / Signal) and the trade log with
realized R-multiples.

The honest caveat from the backend applies here too: without a trade tape
(Dhan), these are OHLCV-derived approximations, not true order flow.

## Session persistence

The user's selection survives a reload via `localStorage`
(`src/lib/storage.ts` — tiny `loadJSON`/`saveJSON`/`usePersistedState`
helpers; the zustand store persists `interval` through its `persist`
middleware with `partialize`):

- **Indicator toggles** (`ntrade.indicators`) — VWAP / Volume Profile /
  Absorption / Strategy on/off.
- **Interval** (`ntrade.chart`) — e.g. `Range` with the **range-size**
  (`ntrade.rangeTicks`) restores exactly.
- **Replay window** (`ntrade.replayWindow`) — the From/To slice, clamped
  into the loaded data range if the contract changed since the save.

A reload does **not** restore the symbol/contract (the front month moves —
the app re-selects today's front contract on boot) nor the live/replay mode.
A user symbol/interval change still resets the replay window and range size
to their defaults — only the initial hydration keeps the persisted values.

## State & race safety

- `useChart` (the renderer's single data hook) aborts in-flight fetches on
  symbol/interval/strategy change **and** discards stale responses via a
  monotonically increasing request id — switching contracts can never flash
  the previous contract's candles. It fetches the full backend-computed
  payload (`/api/market/chart`: candles + VWAP ±σ + volume profile +
  absorptions + strategy markers). The FE never recomputes indicator or
  strategy math (the same backend OverlayPipeline drives `/api/market/chart`,
  the live WS `overlays` patches, and paper/live execution — zero-parity).
- Live candles are cleared on contract/interval change; WS messages for a
  non-current symbol are ignored.
- The chart instance is removed on unmount; the socket is disconnected on
  unmount.

## Assumptions & limitations

- **Timezone:** naive timestamps are IST wall time (the framework's
  convention); the API emits UTC epoch seconds on the wire, and the UI renders
  IST wall-clock deterministically (chart axis, crosshair, and readouts — see
  `src/lib/istTime.ts`).
- **Expired contracts:** listed and selectable-but-disabled in the UI;
  requesting their history still works if the provider has data.
- **Live provider:** the WebSocket pump is a synthetic tick generator seeded
  from the provider's last close, and it is **disabled by default** — the
  server is historical-only unless started with `--live-stream`. For
  production live candles, swap `LiveCandlePump`'s tick source for
  `DhanMarketFeedSource` ticks at the same interface (the pump only talks to
  `MarketDataService`).
- **No auth** — local-only desktop/dev app (per the trading-UI design spec).

## Tests

```bash
# Backend (FastAPI endpoints, provider units, WebSocket)
./.venv/bin/python -m pytest tests/test_marketdata.py tests/test_api_market.py tests/test_live_ws.py

# Frontend (replay state machine)
cd ui && npx vitest run

# Typecheck + build
cd ui && npx tsc -b --noEmit && npm run build
```
