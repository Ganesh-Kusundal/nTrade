# nTrade

Institutional-grade object-oriented trading framework — rich market domain objects on top of [Dhan-Tradehull](https://github.com/dhan-Tradehull/Dhan-Tradehull). Broker transport, websockets, REST and JSON stay hidden behind broker adapters. v0.2.0 · Python 3.10+.

```
ntrade/   framework — brokers, engines, domain, events, data, backtest
api/      FastAPI backend — market data, live pump, paper trading
ui/       React + Vite + TypeScript — futures terminal (TradingView lightweight-charts)
```

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ui]"
```

## Quick start

```bash
# 1. Configure Dhan creds (optional — needed for --provider dhan)
cp .env.example .env   # then set DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, etc.

# 2. Backend (serves API + built UI at http://127.0.0.1:8000)
python -m api --provider synthetic   # synthetic | dhan | parquet
# Add --live-stream to enable the live tick pump (needs market hours + dhan).

# 3. Frontend dev (HMR, proxies /api + /ws to :8000) — optional
cd ui && npm install && npm run dev   # http://localhost:5173
cd ui && npm run build                # production build → ui/dist
```

Providers: `synthetic` (seeded OHLCV, offline) · `dhan` (live broker) · `parquet` (offline `data/ohlcv` store).

## Tests

```bash
pytest -q                 # Python (timeout 120s, coverage floor 90% on ntrade/)
cd ui && npm test         # Frontend (vitest)
```

## Architecture

Domain never imports brokers. `ntrade/domain/ports.py` declares `BrokerAdapter`; brokers implement it. Events flow through `ntrade/kernel/event_bus.py` (RLock, MRO fan-out, causal `correlation_id`). Candle bucketing is UTC-pinned and deterministic for replay/backtest/live parity.

## API

| Endpoint | Notes |
|---|---|
| `GET /api/market/provider` | active provider + instrument-master status |
| `GET /api/market/roots` | futures roots + front-month contracts |
| `GET /api/market/candles?symbol=&interval=1m|5m|15m|1h|1D` | normalized `{time,open,high,low,close,volume}` (time = UTC epoch) |
| `GET /api/market/quote?symbol=` | ltp, change %, day OHLC, volume |
| `GET /api/market/chart?symbol=&strategy=` | single chart payload: candles + overlays + strategy markers |
| `WS /ws/market` | `subscribe`/`unsubscribe` → `candle` + `live_status` |

Wire is UTC epoch; UI renders in IST (`Asia/Kolkata`). See `ui/README.md` for the terminal docs.
