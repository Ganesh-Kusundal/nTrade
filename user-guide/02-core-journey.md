# Core Journey: quotes, history, orders

Once you have a `TradingSession`, everything hangs off instruments — stocks,
indices, options. This page covers the day-to-day loop: get a quote, fetch
history, compute indicators, read an option chain, place an order, and
optionally stream ticks.

All examples use paper mode (no credentials). Swap to
`TradingSession.connect("dhan")` for live.

```python
from datetime import date
from ntrade import TradingSession

session = TradingSession.paper(initial_cash=100_000.0)
```

---

## 1. Instruments

```python
stock = session.stock("TCS")        # equity, NSE
nifty = session.index("NIFTY")      # index
etf   = session.etf("NIFTYBEES")
fut   = session.future(nifty, expiry=date(2026, 8, 27))
opt   = session.option(nifty, strike=25000, expiry=date(2026, 8, 27), option_type="CE")
chain = session.chain(nifty, expiry=0, num_strikes=5)
```

Instruments are **shared**: asking for `"TCS"` twice returns the same object,
so subscriptions and caches are reused.

---

## 2. Market data

```python
nifty.market.refresh()            # pull latest quote (+depth when supported)
nifty.market.ltp()                # last traded price
nifty.market.bid(), nifty.market.ask()
nifty.market.spread(), nifty.market.mid_price()
nifty.market.quote()              # full Quote object

# Historical OHLCV — callable form, cached
series = nifty.market.history()("5m", days=15)
len(series)                       # number of bars
series.df                         # pandas DataFrame (OHLCV)
series.cached, series.is_fresh()
series.refresh()                  # force re-download
```

---

## 3. Analytics

Fetch history **first**, then compute. Without bars the bundle is empty and
`rsi()` / `atr()` return `nan`.

```python
nifty.market.history()("5m", days=5)
nifty.analytics.compute()                  # rsi_14, atr_14, vwap, ...
nifty.analytics.rsi()                      # latest RSI(14)
nifty.analytics.atr()                      # latest ATR(14)
nifty.analytics.statistics()               # last/mean/std/return/volatility
nifty.analytics.supertrend(atr_period=10, multiplier=3.0)
nifty.analytics.indicators                 # the whole bundle dict
```

---

## 4. Option chains & derivatives

```python
chain = nifty.derivatives.option_chain(expiry=0, num_strikes=10)
len(chain)
chain.atm                       # at-the-money Option
chain.atm.greeks.delta
chain.atm.iv
chain.pcr()                     # put/call ratio
chain.max_pain()
```

---

## 5. Orders

Orders go through the instrument's order facade, which routes to the broker:

```python
tcs = session.stock("TCS")

order = tcs.order.buy(75, price=2500, order_type="LIMIT")
order = tcs.order.sell(75, price=2600)
order = tcs.order.market("BUY", 10)
order = tcs.order.limit("BUY", 10, price=2500)
order = tcs.order.stop("SELL", 10, price=2450, trigger_price=2460)
order = tcs.order.cover("SELL", 75, trigger=2500, trigger_price=2480)
order = tcs.order.bracket(
    "BUY", 75, price=2500, target_price=2600, stop_loss_price=2450,
)
```

Inspect the returned `Order`: `order.order_id`, `order.status`,
`order.filled_qty`, `order.avg_price`. Cancel or modify open orders with
`order.cancel()` / `order.modify(price=...)`.

> **Warning:** Live orders go to the real market. Test against the **paper**
> broker first.

---

## 6. Account & portfolio

```python
session.balance()            # available cash
session.positions()          # open positions
session.live_pnl()
session.orderbook()          # OrderBook (typed)
session.tradebook()          # TradeBook (typed)
session.account()            # balance + holdings composite
session.portfolio()          # positions + holdings composite
```

---

## 7. Live streaming (optional)

```python
nifty.stream.subscribe()                       # register with the transport
nifty.stream.on_tick(lambda t: print(t.price))
nifty.stream.ticks(limit=10)                   # buffered ticks
nifty.stream.is_live
```

---

Next: [Writing Strategies](03-strategies.md) · Back: [Install & Brokers](01-install-brokers.md) · [Index](index.md)
