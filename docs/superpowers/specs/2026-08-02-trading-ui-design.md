# nTrade Trading UI — Design Spec

**Date**: 2026-08-02
**Status**: Approved
**Approach**: PyWebView + React/Vite + FastAPI (Approach B)

## 1. Overview

Build an all-in-one desktop trading UI for the nTrade Python trading framework. The UI provides live monitoring, order management, backtest/research, risk dashboards, and portfolio tracking — wrapping the existing event-driven kernel in a modern desktop experience.

**Form factor**: Desktop app (native window via PyWebView)
**Stack**: Python (FastAPI) + React/TypeScript (Vite) + TradingView lightweight-charts
**Launch**: Single `python main.py` starts kernel, API server, and native window

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  PyWebView Native Window (1400×900 default)              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  React SPA (Vite dev / static prod build)          │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐          │  │
│  │  │Dashboard │ │ Watchlist│ │ Orders   │ ...      │  │
│  │  │          │ │ + Charts │ │ + Book   │          │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘          │  │
│  │       └─────────────┼────────────┘                 │  │
│  │                     │ REST + WebSocket              │  │
│  └─────────────────────┼──────────────────────────────┘  │
├────────────────────────┼─────────────────────────────────┤
│  FastAPI (localhost:8000)                                │
│  ├─ REST endpoints     (GET/POST/PUT/DELETE)             │
│  ├─ WebSocket /ws      (real-time event stream)          │
│  └─ KernelBridge       (EventBus → JSON → WebSocket)     │
├──────────────────────────────────────────────────────────┤
│  ntrade kernel (same Python process)                     │
│  TradingKernel · EventBus · LiveRunner · Strategies      │
└──────────────────────────────────────────────────────────┘
```

### Key Decisions

- **Single Python process**: FastAPI + kernel co-locate. PyWebView opens the React frontend. One command launches everything.
- **KernelBridge**: A thin ~100-line adapter subscribes to the kernel's `EventBus` and translates domain events into JSON messages broadcast over WebSocket. The kernel is not modified.
- **Decoupled frontend**: React communicates only via REST + WebSocket. No direct kernel access. The frontend is identical whether running against PyWebView, a browser, or Tauri.
- **Dev/prod parity**: Dev mode runs Vite HMR + FastAPI separately. Prod mode builds static files served by FastAPI.

## 3. Screens

### 3.1 Dashboard (home)
- Portfolio summary cards: balance, equity, P&L (realized + unrealized)
- Equity curve sparkline (intraday)
- Open positions table (AG Grid): symbol, qty, avg price, LTP, P&L
- Session status: mode (live/paper/replay), broker connection, heartbeat, feed status

### 3.2 Watchlist + Chart
- Left panel: watchlist with live LTP, change %, add/remove symbols
- Right: TradingView lightweight-charts candlestick chart
  - Timeframe selector: 1m, 5m, 15m, 1h, 1D
  - Overlays: EMA/SMA bands, volume bars
- Bottom: quick order entry (buy/sell, quantity, order type, price for limit)

### 3.3 Order Book
- Three tabs: Open Orders | Trade History | Order History
- Open Orders: live status (PENDING, PARTIAL, FILLED, REJECTED), modify/cancel buttons
- Trade History: fills with timestamp, price, quantity, per-trade P&L
- Order History: full audit trail

### 3.4 Backtest Lab
- Left: strategy selector + parameter form (EMA fast/slow, quantity, symbol, date range)
- Right: results panel
  - Equity curve chart
  - Drawdown chart
  - Summary stats: total return, max drawdown %, Sharpe ratio, win rate, # trades, commissions
  - Trade log table

### 3.5 Risk Monitor
- Circuit breaker status: daily loss, drawdown %, price deviation (green/red indicators)
- Real-time exposure: per-symbol notional, total market exposure
- Kill switch: activate/deactivate button
- Session heartbeat + feed connection status

### Navigation
- Left sidebar (collapsible) with screen icons + labels
- Keyboard shortcuts: 1–5 for screens, `Esc` to deselect

## 4. API Design

### 4.1 REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/account` | `{ balance, equity, pnl }` |
| GET | `/api/positions` | `[ { symbol, qty, avg_price, ltp, pnl } ]` |
| GET | `/api/orders` | `[ { id, symbol, side, type, qty, price, status } ]` |
| GET | `/api/orders/{id}` | Full order detail |
| POST | `/api/orders` | Place order `{ symbol, side, type, qty, price? }` |
| PUT | `/api/orders/{id}` | Modify order `{ qty?, price? }` |
| DELETE | `/api/orders/{id}` | Cancel order |
| GET | `/api/watchlist` | `[ { symbol, exchange } ]` |
| POST | `/api/watchlist` | Add symbol |
| DELETE | `/api/watchlist/{symbol}` | Remove symbol |
| GET | `/api/watchlist/{symbol}/quotes` | Current quote |
| GET | `/api/backtest/run` | Run backtest (query params) |
| GET | `/api/backtest/{id}` | Backtest result |
| GET | `/api/risk/status` | `{ breakers, exposure, halted }` |
| POST | `/api/risk/halt` | Manual kill switch |
| POST | `/api/risk/resume` | Resume after halt |
| GET | `/api/session/status` | `{ mode, connected, heartbeat, feed_status }` |

### 4.2 WebSocket Channel

Single persistent WebSocket at `/ws`. Server pushes JSON:

```json
{ "type": "quote",     "symbol": "NIFTY", "ltp": 22450.5, "bid": 22450, "ask": 22451, "ts": "..." }
{ "type": "order",     "id": "ORD-123", "status": "FILLED", "fill_price": 22430, "ts": "..." }
{ "type": "position",  "symbol": "NIFTY", "quantity": 5, "avg_price": 22430, "pnl": 1250 }
{ "type": "account",   "balance": 98432, "equity": 102150 }
{ "type": "risk",      "breaker": "max_daily_loss", "triggered": true }
{ "type": "heartbeat", "ts": "...", "ticks": 1523, "uptime": 3600 }
```

Client sends commands:
```json
{ "type": "subscribe",   "symbols": ["NIFTY", "RELIANCE"] }
{ "type": "unsubscribe", "symbols": ["RELIANCE"] }
```

## 5. KernelBridge

The bridge is the only new code that touches the kernel. It:
1. Subscribes to the kernel's `EventBus`
2. Translates domain events into JSON messages
3. Broadcasts to all connected WebSocket clients

Event mapping:
| Kernel Event | WebSocket Message |
|---|---|
| `QuoteUpdatedEvent` | `{ "type": "quote", ... }` |
| `OrderAcceptedEvent` | `{ "type": "order", "status": "ACCEPTED", ... }` |
| `OrderFilledEvent` | `{ "type": "order", "status": "FILLED", ... }` |
| `OrderRejectedEvent` | `{ "type": "order", "status": "REJECTED", ... }` |
| `OrderUpdatedEvent` | `{ "type": "order", "status": "UPDATED", ... }` |
| `PositionUpdatedEvent` | `{ "type": "position", ... }` |
| `BalanceChangedEvent` | `{ "type": "account", ... }` |
| `RiskHaltedEvent` | `{ "type": "risk", "triggered": true, ... }` |
| `RiskResumedEvent` | `{ "type": "risk", "triggered": false, ... }` |
| `HeartbeatEvent` | `{ "type": "heartbeat", ... }` |
| `FeedDisconnectedEvent` | `{ "type": "feed", "status": "disconnected" }` |

## 6. Frontend Tech Stack

| Concern | Library | Version | Why |
|---|---|---|---|
| Framework | React | 18+ | Ecosystem, component reuse |
| Build | Vite | 5+ | Fast HMR, zero-config |
| Language | TypeScript | 5+ | Type safety for API contracts |
| Charts | lightweight-charts | 4+ | TradingView, purpose-built for financial data |
| Tables | ag-grid-react (community) | 31+ | Real-time updates, sorting, filtering |
| State | zustand | 4+ | 1KB, no boilerplate, WebSocket-friendly |
| Styling | Tailwind CSS | 3+ | Utility-first, fast iteration |
| Layout | CSS Grid | native | Responsive panels, no library needed |
| Routing | react-router | 6+ | Standard SPA routing |
| HTTP | native fetch | — | No axios needed for this scope |

## 7. Project Structure

```
nTrade/
├── ntrade/                  # existing kernel (untouched)
├── ui/                      # NEW — React frontend
│   ├── src/
│   │   ├── App.tsx          # router + layout
│   │   ├── main.tsx         # entry point
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Watchlist.tsx
│   │   │   ├── Orders.tsx
│   │   │   ├── Backtest.tsx
│   │   │   └── Risk.tsx
│   │   ├── components/
│   │   │   ├── Chart.tsx         # lightweight-charts wrapper
│   │   │   ├── PositionsTable.tsx # AG Grid wrapper
│   │   │   ├── OrderEntry.tsx    # buy/sell form
│   │   │   ├── NavSidebar.tsx
│   │   │   └── StatusBadge.tsx   # connection indicator
│   │   ├── store/
│   │   │   └── useStore.ts      # Zustand store
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts  # WebSocket + auto-reconnect
│   │   └── api/
│   │       └── client.ts        # REST client
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── package.json
│   └── tsconfig.json
├── api/                     # NEW — FastAPI backend
│   ├── __init__.py
│   ├── server.py            # FastAPI app + static serving
│   ├── routes.py            # REST handlers
│   ├── websocket.py         # WebSocket manager
│   └── bridge.py            # KernelBridge
├── main.py                  # NEW — single launcher
└── scripts/                 # existing CLI scripts (unchanged)
```

## 8. Launcher (`main.py`)

```python
import threading, webview
from api.server import create_app
from ntrade.kernel.session import TradingSession

session = TradingSession.connect("dhan")
app = create_app(session)

thread = threading.Thread(
    target=lambda: uvicorn.run(app, host="127.0.0.1", port=8000),
    daemon=True
)
thread.start()

webview.create_window("nTrade", "http://localhost:8000", width=1400, height=900)
webview.start()
```

## 9. Build & Run

### Development
```bash
# Terminal 1: Python backend (FastAPI with hot-reload)
python api/server.py

# Terminal 2: React frontend (Vite HMR)
cd ui && npm run dev
```

### Production
```bash
cd ui && npm run build        # → ui/dist/
python main.py                # FastAPI serves static + API, PyWebView opens window
```

## 10. Out of Scope (YAGNI)

- No authentication (local-only desktop app)
- No multi-user support
- No cloud deployment
- No mobile responsive (desktop window only)
- No plugin system
- No custom indicator builder UI
- No options chain visualization (v1)
- No multi-broker support in UI (backend supports it, UI hardcodes one broker per session)

## 11. Dependencies Added

### Python
- `fastapi>=0.110`
- `uvicorn[standard]>=0.29`
- `websockets>=12.0`
- `pywebview>=5.0`

### Node.js (ui/)
- `react`, `react-dom`, `react-router-dom`
- `lightweight-charts`
- `ag-grid-react`, `ag-grid-community`
- `zustand`
- `tailwindcss`, `@tailwindcss/vite`
- `typescript`, `vite`, `@vitejs/plugin-react`
