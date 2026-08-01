# ntrade — Broker Interface Guide

Everything in ntrade hangs off one idea: **you talk to market objects, never to
REST endpoints or JSON**. Broker transport (Dhan REST/websocket, auth, token
refresh) is hidden behind a `BrokerAdapter`. Swapping brokers does not change
your code.

```python
session = TradingSession.connect("dhan")   # live Dhan
nifty   = session.index("NIFTY")
nifty.market.refresh()                      # pull a fresh quote

paper   = TradingSession.paper()            # deterministic offline broker
tcs     = paper.stock("TCS")
```

---

## 1. Install & first import

```bash
pip install -e .            # from the repo root (pandas, numpy, Dhan-Tradehull)
```

For a live Dhan session you also need a `.env` file in the working directory
with your Dhan credentials (the auth layer reads it; see
`ntrade/brokers/dhan_auth.py`). Paper trading needs **no** credentials.

```python
from ntrade import TradingSession
```

---

## 2. Choosing a broker

The broker registry maps a name to a factory. `available()` lists what is
registered (Dhan auto-registers only if `Dhan-Tradehull` is installed):

```python
from ntrade import BrokerRegistry

BrokerRegistry.available()          # ['dhan', 'paper']

broker = BrokerRegistry.get("paper")   # construct an adapter directly
broker = BrokerRegistry.get("dhan", env_path=".env")
```

| Broker | Name    | Needs credentials | Use for                                   |
|--------|---------|-------------------|-------------------------------------------|
| Dhan   | `"dhan"`| yes (`.env`)      | live quotes, history, orders, option chains |
| Paper  | `"paper"`| no               | tests, backtests, replays — deterministic  |

---

## 3. The session: preferred entry point

`TradingSession` is the one-stop API. Three constructors cover the three modes:

```python
# Live — named broker, `.env` credentials
session = TradingSession.connect("dhan")

# Paper — deterministic, offline, seeded RNG
session = TradingSession.paper(initial_cash=100_000.0)

# Replay — feed previously recorded events through the kernel
session = TradingSession.replay(events)
```

Everything below is mode-independent: the same calls work live and on paper.

### Instrument access

```python
stock     = session.stock("TCS")        # equity, NSE
nifty     = session.index("NIFTY")      # index
etf       = session.etf("NIFTYBEES")
fut       = session.future(nifty, expiry=date(2026, 8, 27))
opt       = session.option(nifty, strike=25000, expiry=date(2026, 8, 27), option_type="CE")
chain     = session.chain(nifty, expiry=0, num_strikes=5)   # option chain
```

Instruments are **shared** (flyweight): asking for `"TCS"` twice returns the
same object, so subscriptions and caches are reused.

### Market data

```python
nifty.market.refresh()            # pull latest quote (+depth when supported)
nifty.market.ltp()                # 24325.5
nifty.market.bid(), nifty.market.ask()
nifty.market.spread(), nifty.market.mid_price()
nifty.market.quote()              # full Quote object

# Historical OHLCV — callable form, cached
series = nifty.market.history()("5m", days=15)
len(series)                       # number of bars
series.df                         # pandas DataFrame (timestamp/open/high/low/close/volume)
series.cached, series.is_fresh()
series.refresh()                  # force re-download
```

### Analytics (indicators, stats)

Compute an indicator bundle, then read scalars:

```python
nifty.analytics.compute()                  # rsi_14, atr_14, vwap, ...
nifty.analytics.rsi()                      # latest RSI(14)
nifty.analytics.atr()                      # latest ATR(14)
nifty.analytics.statistics()               # last/mean/std/return/volatility
nifty.analytics.supertrend(atr_period=10, multiplier=3.0)
nifty.analytics.indicators                 # the whole bundle dict
```Analytics read from the instrument's **history** — fetch bars first (`.history()("5m", days=5)`) before `compute()`, or the bundle comes back empty and `rsi()`/`atr()` return `nan`. (`compute_indicators()` shown in older scripts is a stale spelling — the canonical method is `nifty.analytics.compute()`.)

### Option chains & derivatives

```python
chain = nifty.derivatives.option_chain(expiry=0, num_strikes=10)
len(chain)                      # number of options
chain.atm                       # at-the-money Option
chain.atm.greeks.delta          # 0.52
chain.atm.iv
chain.pcr()                     # put/call ratio
chain.max_pain()
```

### Orders

Two styles, both routed through the broker adapter:

```python
# 1) Facade — reads naturally
order = tcs.order.buy(75, price=2500, order_type="LIMIT")
order = tcs.order.market("BUY", 10)
order = tcs.order.bracket("BUY", 75, price=2500, target_price=2600, stop_loss_price=2450)

# 2) Fluent builder
order = tcs.trade.buy().market().quantity(100).product("MIS").place()
```

Inspect the returned `Order`: `order.order_id`, `order.status`,
`order.filled_qty`, `order.avg_price`. Cancel/modify open orders with
`order.cancel()` / `order.modify(price=...)`.

> ⚠️ Live orders go to the real market. Test against the **paper** broker first.

### Account & portfolio

```python
session.balance()            # available cash
session.positions()          # open positions
session.live_pnl()
session.orderbook()          # OrderBook (typed)
session.tradebook()          # TradeBook (typed)
session.account()            # balance + holdings composite
session.portfolio()          # positions + holdings composite
```

### Live streaming (optional)

```python
nifty.stream.subscribe()                       # register with the transport
nifty.stream.on_tick(lambda t: print(t.price))
nifty.stream.ticks(limit=10)                   # buffered ticks
nifty.stream.is_live
```

---

## 4. The legacy `Market` facade

`Market` predates `TradingSession` and still works — it is a thin wrapper, so
prefer `TradingSession` for new code:

```python
from ntrade import Market

m = Market(broker="dhan")        # live
m = Market(broker="paper")       # offline
nifty = m.index("NIFTY")
nifty.market.refresh()
m.balance()
m.connect()
```

---

## 5. In a Jupyter notebook

The package is importable from the repo root. If it is not installed, put the
repo on the path in the first cell:

```python
# Cell 0 — environment setup
import sys
sys.path.insert(0, "/path/to/nTrade")     # repo root
%load_ext autoreload
%autoreload 2

from ntrade import TradingSession
```

```python
# Cell 1 — paper session (offline, deterministic — safe to play with)
session = TradingSession.paper(initial_cash=100_000.0)
tcs = session.stock("TCS")

tcs.market.refresh()
print("LTP:", tcs.market.ltp())

series = tcs.market.history()("5m", days=5)
series.df.tail()
```

```python
# Cell 2 — live session (needs .env with Dhan credentials)
session = TradingSession.connect("dhan")
nifty = session.index("NIFTY")
nifty.market.refresh()
series = nifty.market.history()("5m", days=5)     # fetch history FIRST —
nifty.analytics.compute()                          # indicators compute over it
print("NIFTY:", nifty.market.ltp(), "| RSI:", round(nifty.analytics.rsi(), 2))

chain = nifty.derivatives.option_chain(num_strikes=5)
print("ATM:", chain.atm.symbol, "delta:", chain.atm.greeks.delta)
```

Notebook tips:

- `session.balance()` and `session.positions()` are the safest live calls —
  they are read-only.
- `autoreload` keeps instrument classes in sync after `pip install -e .`
  edits; `symbol` caches are per-process, so restart the kernel after
  changing instrument code.
- Keep one session per notebook; do not create a fresh `TradingSession`
  inside a loop (broker connections and subscriptions are not free).
