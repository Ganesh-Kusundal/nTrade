# nTrade Trading UI — Implementation Plan

## Design Spec
`docs/superpowers/specs/2026-08-02-trading-ui-design.md`

## Design System (UI/UX Pro Max)
- **Colors**: Background `#020617`, Primary `#0F172A`, Foreground `#F8FAFC`, Accent `#22C55E` (green), Destructive `#EF4444` (red), Border `#334155`, Muted `#1A1E2F`
- **Typography**: Inter (300–700), tabular figures for data columns
- **Charts**: TradingView lightweight-charts — Bullish `#26A69A`, Bearish `#EF5350`, volume 40% opacity
- **Style**: Dark terminal aesthetic, SVG icons (Lucide), 44×44px touch targets, focus rings 3px, `prefers-reduced-motion` respected

## Architecture Decisions (Synthesized from 3 Agent Plans)

### A1: Zero Kernel Modification
All new code in `api/`, `ui/`, `main.py`, `tests/test_api_*.py`. The existing `ntrade/` package and its 638+ tests remain untouched.

### A2: Manual Order Placement via Signal Pipeline
`POST /api/orders` publishes `SignalGeneratedEvent(strategy="manual")` to the kernel's EventBus. This flows through the existing pipeline:
```
SignalGeneratedEvent → RiskEngine.on_signal() → SignalApprovedEvent
  → OrderEngine.on_signal_approved() → OrderIntentEvent → ExecutionRouter.submit()
  → BrokerExecution/SimulatedExecution → OrderAcceptedEvent/OrderFilledEvent
```
This reuses all existing risk checks (circuit breakers, limits, fat-finger guards) — no new order-plumbing code needed.

### A3: KernelBridge with Thread-Safe Queue + Quote Throttling
The EventBus is **synchronous under RLock** (L57 of `event_bus.py`). The bridge handler must be O(1) — just `queue.Queue.put_nowait()`. An async drain loop handles serialization + WebSocket broadcast. Quote events are throttled to 10Hz per symbol to handle event amplification (1 tick → 5-15 downstream events).

### A4: Frontend rAF-Batched Quote Updates
React store uses `requestAnimationFrame` batching for high-frequency quote updates, preventing 60+ re-renders/sec per instrument.

---

## Task Breakdown

### Phase 1: Backend Foundation (api/)

#### Task 1.1: Dependencies & Scaffolding
- **Files**: `pyproject.toml`, `api/__init__.py`
- Add optional `[ui]` dependency group:
  ```toml
  [project.optional-dependencies]
  ui = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "websockets>=12.0", "pywebview>=5.0"]
  ```
- Create empty `api/__init__.py`
- **Validation**: `pip install -e ".[ui]"` succeeds

#### Task 1.2: WebSocket Manager
- **File**: `api/websocket.py` (~50 lines)
- `WebSocketManager` class: `set[WebSocket]`, `connect()`, `disconnect()`, `broadcast(dict)`
- Per-client symbol subscription sets (client sends `{type: "subscribe", symbols: [...]}`)
- Dead connection cleanup on broadcast failure
- **Validation**: Manual test with `websocat ws://localhost:8000/ws`

#### Task 1.3: KernelBridge — EventBus → WebSocket
- **File**: `api/bridge.py` (~120 lines)
- Subscribe to specific UI-relevant event types (NOT base Event — avoid noise):
  - Quote: `QuoteUpdatedEvent`
  - Orders: `OrderAcceptedEvent`, `OrderFilledEvent`, `OrderRejectedEvent`, `OrderUpdatedEvent`
  - Portfolio: `PositionUpdatedEvent`, `BalanceChangedEvent`
  - Risk: `RiskHaltedEvent`, `RiskResumedEvent`
  - Lifecycle: `HeartbeatEvent`, `FeedDisconnectedEvent`
  - Candles: `CandleClosedEvent`
- Sync handler: `queue.Queue.put_nowait(event)` — O(1), no I/O, ~100ns
- Per-symbol quote throttle: `time.monotonic()` check, 10Hz max, last-value-wins
- Async `drain_loop()`: batch dequeue → serialize → `ws.broadcast()`
- Serialization: manual field-pick functions per event type (faster than `dataclasses.asdict()`)
  - `datetime` → ISO 8601 string
  - `Enum` → `.value`
- `detach()` method for clean shutdown
- **Validation**: Unit test — publish events to bus, verify they appear in queue with correct serialization

#### Task 1.4: REST Routes
- **File**: `api/routes.py` (~200 lines)
- `AppContext` dataclass: `session`, `kernel`, `ws_manager`, `bridge`
- Routes receive `AppContext` via FastAPI dependency injection
- Key implementations:
  | Endpoint | Source |
  |----------|--------|
  | `GET /api/account` | `session.balance()`, `session.account().as_dict()`, `kernel.risk_engine.equity()` |
  | `GET /api/positions` | `[p.as_dict() for p in session.positions()]` with LTP from `ctx.portfolio` |
  | `GET /api/orders` | `session.orderbook()` or `kernel.open_orders()` |
  | `POST /api/orders` | Publish `SignalGeneratedEvent(strategy="manual")` to `kernel.bus` |
  | `PUT /api/orders/{id}` | `kernel.modify_order(id, **body)` |
  | `DELETE /api/orders/{id}` | `kernel.cancel_order(id)` |
  | `GET /api/watchlist` | `kernel.ctx.instruments_snapshot()` → symbol list |
  | `POST /api/watchlist` | `session.register(instrument)` |
  | `GET /api/watchlist/{symbol}/quotes` | `session.broker.get_quote(instrument)` |
  | `GET /api/backtest/run` | `BacktestSimulator` in `asyncio.to_thread()` — downsample equity_curve to 500pts |
  | `GET /api/risk/status` | `kernel.risk_engine.{halted, halt_reason, equity(), max_daily_loss, max_drawdown_pct}` |
  | `POST /api/risk/halt` | `kernel.risk_engine.halt("manual")` |
  | `POST /api/risk/resume` | `kernel.risk_engine.resume()` |
  | `GET /api/session/status` | `{mode, connected, session_id}` |
- Thread safety: reads from `ctx` use `ctx.lock` where documented
- **Validation**: `pytest tests/test_api_routes.py` with `httpx.AsyncClient` + `TradingSession.paper()`

#### Task 1.5: FastAPI Application
- **File**: `api/server.py` (~80 lines)
- `create_app(session: TradingSession) -> FastAPI` factory
- CORS middleware (dev: all origins; prod: localhost only)
- Static file mount: serve `ui/dist/` at `/` when it exists (prod mode)
- Lifespan: start bridge, create drain_loop task
- Dev mode: `uvicorn api.server:dev_app --reload` with a pre-configured paper session
- **Validation**: `GET /api/session/status` returns 200

#### Task 1.6: PyWebView Launcher
- **File**: `main.py` (~35 lines)
- `TradingSession.connect("dhan")` → `connect_broker()` → `create_app(session)`
- uvicorn in `threading.Thread(daemon=True)`
- Health-check poll before opening window (wait for `/api/session/status` 200)
- `webview.create_window("nTrade", "http://localhost:8000", width=1400, height=900)`
- `webview.start()` (blocking, main thread)
- **Validation**: `python main.py` opens native window with API responding

---

### Phase 2: Frontend (ui/)

#### Task 2.1: React + Vite + TypeScript Scaffolding
- **Files**: `ui/package.json`, `ui/vite.config.ts`, `ui/tsconfig.json`, `ui/tailwind.config.ts`, `ui/index.html`, `ui/src/main.tsx`, `ui/src/App.tsx`
- `npm create vite@latest ui -- --template react-ts`
- Install: `react-router-dom`, `zustand`, `lightweight-charts`, `ag-grid-react`, `ag-grid-community`, `tailwindcss`, `@tailwindcss/vite`, `lucide-react` (icons)
- Vite proxy: `/api` → `http://localhost:8000`, `/ws` → `ws://localhost:8000`
- Tailwind dark theme with design system colors
- `App.tsx`: React Router with 5 routes + `NavSidebar` layout shell
- **Validation**: `npm run dev` shows sidebar navigation between 5 placeholder pages

#### Task 2.2: API Client + WebSocket Hook
- **Files**: `ui/src/api/client.ts`, `ui/src/hooks/useWebSocket.ts`
- `client.ts`: Typed `fetch` wrappers for all REST endpoints
- `useWebSocket.ts`:
  - Connect to `ws://localhost:8000/ws`
  - Auto-reconnect: exponential backoff (1s → 2s → 4s → ... → 30s max, ±500ms jitter)
  - Parse JSON, dispatch to Zustand store by `msg.type`
  - Expose `connectionStatus: "connected" | "disconnected" | "reconnecting"`
  - Send `subscribe`/`unsubscribe` on watchlist changes
- **Validation**: Console log shows incoming WS messages

#### Task 2.3: Zustand Store
- **File**: `ui/src/store/useStore.ts` (~150 lines)
- Slices: `account`, `positions`, `orders`, `quotes`, `risk`, `session`, `watchlist`
- `updateFromWS(msg)` — single dispatcher, switches on `msg.type`
- Quote updates: accumulate in a `ref`, flush to store on `requestAnimationFrame` (prevents render storm)
- Initial state: `fetchAccount()`, `fetchPositions()`, `fetchOrders()` on mount, then WS takes over
- **Validation**: Store updates visible in React DevTools

#### Task 2.4: Dashboard Screen
- **Files**: `ui/src/pages/Dashboard.tsx`, `ui/src/components/PositionsTable.tsx`, `ui/src/components/StatusBadge.tsx`
- Portfolio summary cards: balance, equity, P&L (color-coded green/red)
- AG Grid positions table: symbol, qty, avg_price, ltp, pnl — real-time row updates from WS
- Session status badge: mode, connected indicator, heartbeat age
- **Validation**: Run against paper session, verify data renders and updates

#### Task 2.5: Watchlist + Chart Screen
- **Files**: `ui/src/pages/Watchlist.tsx`, `ui/src/components/Chart.tsx`, `ui/src/components/OrderEntry.tsx`
- Left panel: watchlist with live LTP, change %, add/remove (REST + WS)
- `Chart.tsx`: TradingView lightweight-charts wrapper
  - Candlestick series + volume histogram
  - Timeframe selector (1m, 5m, 15m, 1h, 1D)
  - Initial data via REST, live updates via WS `candle` messages
  - EMA overlay from indicator data
- `OrderEntry.tsx`: buy/sell form (symbol, side, qty, order_type, price for limit)
  - Submit → `POST /api/orders` → signal flows through risk→OMS
  - Confirmation toast on fill/reject via WS
- **Validation**: Add NIFTY, see candle chart, place paper order

#### Task 2.6: Orders Screen
- **File**: `ui/src/pages/Orders.tsx`
- Three tabs: Open Orders | Trade History | Order History
- AG Grid tables with live status from WS `order` messages
- Open Orders: modify/cancel buttons → `PUT/DELETE /api/orders/{id}`
- Status badges: PENDING (yellow), PARTIAL (blue), FILLED (green), REJECTED (red)
- **Validation**: Place order from Watchlist, see it in Open Orders with live status

#### Task 2.7: Backtest Lab Screen
- **File**: `ui/src/pages/Backtest.tsx`
- Left: strategy selector (EMA Cross initially), parameter form (fast, slow, qty, symbol, date range)
- "Run Backtest" → `GET /api/backtest/run?...`
- Right: results panel
  - Equity curve (lightweight-charts line series, downsampled to 500pts)
  - Drawdown chart (area chart, inverted)
  - Summary stats cards: total return, max drawdown, win rate, # trades, commissions
  - Trade log AG Grid table
- Loading spinner while backtest runs (async API call)
- **Validation**: Run EMA cross on NIFTY, verify equity curve and trade log

#### Task 2.8: Risk Monitor Screen
- **File**: `ui/src/pages/Risk.tsx`
- Circuit breaker cards: daily loss, drawdown %, price deviation (green/red indicators)
- Exposure table: per-symbol notional from positions
- Kill switch: Activate button (red, confirmation dialog) → `POST /api/risk/halt`
  - Resume button (green) → `POST /api/risk/resume`
- Feed connection status from WS `feed` messages
- **Validation**: Halt risk, verify UI shows red; resume, verify green

#### Task 2.9: Navigation Sidebar + Layout Polish
- **File**: `ui/src/components/NavSidebar.tsx`
- Collapsible left sidebar with Lucide icons + labels
- Keyboard shortcuts: 1-5 for screens, Esc to deselect
- Active screen highlighted with accent color
- nTrade logo/name at top
- Connection status dot at bottom (green/red from WS hook)
- **Validation**: All shortcuts work, sidebar collapses/expands

---

### Phase 3: Integration & Testing

#### Task 3.1: API Integration Tests
- **File**: `tests/test_api_routes.py` (~150 lines)
- Use `httpx.AsyncClient` + `create_app(TradingSession.paper())`
- Test every REST endpoint:
  - Account returns balance/equity
  - Positions reflect paper trades
  - Order placement → signal → approval → fill (full pipeline)
  - Order modify/cancel
  - Risk halt/resume
  - Session status
  - Backtest run with synthetic data
- **Validation**: All tests pass

#### Task 3.2: KernelBridge Tests
- **File**: `tests/test_api_bridge.py` (~100 lines)
- Test serialization: every event type → correct JSON shape
- Test throttle: publish 100 quotes in 100ms, verify only ~10 per symbol forwarded
- Test queue: publish events, verify drain_loop broadcasts them
- Test detach: after detach, no more events enqueued
- **Validation**: All tests pass

#### Task 3.3: WebSocket Tests
- **File**: `tests/test_api_websocket.py` (~80 lines)
- Test connect/disconnect lifecycle
- Test broadcast reaches all connected clients
- Test subscribe/unsubscribe filters quotes by symbol
- Test dead connection cleanup
- **Validation**: All tests pass

#### Task 3.4: End-to-End Smoke Test
- **File**: `tests/test_e2e_ui.py` (~50 lines)
- Start paper session + FastAPI + bridge
- Connect WebSocket client
- Place order via REST → verify WS receives order events
- Run backtest via REST → verify result shape
- Halt risk → verify WS receives risk event
- **Validation**: Full pipeline verified end-to-end

#### Task 3.5: Existing Test Regression
- Run full `pytest` suite — all 638+ existing tests must pass unchanged
- **Validation**: `pytest tests/` — 0 failures

---

## Dependency Graph

```
Task 1.1 (deps)
  ├─→ Task 1.2 (WebSocket manager)
  │     └─→ Task 1.3 (KernelBridge)
  └─→ Task 1.4 (REST routes)
        └─→ Task 1.5 (FastAPI app) ← also depends on 1.2, 1.3
              └─→ Task 1.6 (Launcher)

Task 2.1 (React scaffold) — independent of backend
  ├─→ Task 2.2 (API client + WS hook)
  │     └─→ Task 2.3 (Zustand store)
  │           ├─→ Task 2.4 (Dashboard)
  │           ├─→ Task 2.5 (Watchlist + Chart)
  │           ├─→ Task 2.6 (Orders)
  │           ├─→ Task 2.7 (Backtest Lab)
  │           └─→ Task 2.8 (Risk Monitor)
  └─→ Task 2.9 (NavSidebar) — can start early

Tasks 2.4–2.8 are independent of each other (parallelizable)

Task 3.1–3.4 depend on Tasks 1.5 + 1.6 (backend complete)
Task 3.5 runs anytime (no dependencies)
```

## Rejected Alternatives

| Alternative | Why Rejected |
|-------------|-------------|
| NiceGUI (pure Python UI) | Ceiling on polish — can't build a professional trading terminal. Limited chart/layout control. |
| Tauri + React (Rust shell) | Rust toolchain adds complexity. PyWebView gives identical frontend with 5 lines of Python. Can migrate later. |
| Electron + React | 150MB+ binary, high memory. Overkill for a local-only trading UI. |
| Subscribing to base `Event` class | Catches ALL events (TickEvent, IndicatorUpdatedEvent, etc.) — most are noise. Subscribing to specific types is cleaner and more performant. |
| `dataclasses.asdict()` for serialization | Slow at high event rates, includes unnecessary fields. Manual field-pick functions are 5-10× faster. |
| Direct `place_order()` on broker | Bypasses risk engine entirely. Using `SignalGeneratedEvent` reuses all circuit breakers and limits. |
| Axios for HTTP | Native `fetch` is sufficient for this scope. One less dependency. |

## Risk Register

| Risk | Mitigation |
|------|-----------|
| EventBus handler blocks kernel dispatch | Handler only does `queue.put_nowait()` — O(1), ~100ns. No I/O. |
| Quote flood overwhelms WebSocket | Per-symbol 10Hz throttle in bridge. Frontend rAF batching. |
| Backtest blocks event loop | Run in `asyncio.to_thread()`. Downsample equity_curve to 500pts. |
| PyWebView opens before server ready | Health-check poll loop in `main.py` before `create_window()`. |
| Thread safety on ctx reads | Use `ctx.lock` for reads that need consistency. `TradingSession` methods already handle broker thread-safety. |
| Existing tests break | Zero changes to `ntrade/`. New code in `api/`, `ui/`. `testpaths = ["tests"]` in pyproject.toml. |
| WebSocket reconnection storms | Exponential backoff with jitter (1s → 30s max, ±500ms random). |
| datetime/Enum serialization fails | Central `_serialize()` handles ISO 8601 for datetime, `.value` for Enum. Unit test covers every event type. |
