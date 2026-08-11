# Broker Capabilities (Dhan extras)

Every instrument talks through a broker adapter. The **core** API
(`.market.refresh()`, `.order.buy()`, `.history()`, …) works on both paper and
Dhan. **Capabilities** are broker extras — things only Dhan (or another live
broker) can do. You call them the same way:

```python
nifty.broker.depth20()
nifty.broker.place_super_order(...)
nifty.broker.kill_switch(action="ACTIVATE")
```

If the current broker does not support a capability, you get a clear
`AttributeError` — fail fast, no silent no-ops.

```python
from ntrade import TradingSession

# Paper — core API only; Dhan-only capabilities raise AttributeError
paper = TradingSession.paper()
tcs = paper.stock("TCS")
tcs.market.refresh()                 # works
# tcs.broker.depth20()               # AttributeError on paper

# Live Dhan — core + capabilities
session = TradingSession.connect("dhan")
nifty = session.index("NIFTY")
nifty.market.refresh()
depth = nifty.broker.depth20(levels=20)
```

---

## What paper vs Dhan can do

| Area | Paper | Dhan |
|------|-------|------|
| Quotes / history / orders (limit, market, stop, cover, bracket via facade) | yes | yes |
| Option chains | yes (seeded) | yes (live) |
| Cancel / modify / orderbook / positions / balance | yes | yes |
| 20-level depth, margin calc, kill switch | no | yes |
| Super / slice / forever / conditional orders | no | yes |
| ATM/ITM/OTM strike helpers, expiry list, lot size | no | yes |
| Long-term history, OHLC bundle, instrument file | no | yes |

Rule of thumb: develop and backtest on **paper**; use Dhan capabilities only
when you need live depth, advanced order types, or broker-side protection.

---

## Market-data capabilities

### Depth (`depth20`)

```python
depth = nifty.broker.depth20(levels=20)
depth.bids[0].price, depth.bids[0].quantity
depth.asks[0].price, depth.asks[0].quantity
```

Use when you need the order book, not just LTP/bid/ask.

### OHLC / long-term history / start date

```python
nifty.broker.ohlc()                              # tick-level intraday OHLC bundle
nifty.broker.long_term_history(
    timeframe="1d", from_date="2024-01-01", to_date="2024-12-31",
)
nifty.broker.start_date()                        # earliest date Dhan has
nifty.broker.instrument_file()                   # path to instrument master
```

Prefer `.market.history()("5m", days=15)` for the normal path. Use
`long_term_history` when you need a multi-year daily series with an explicit
date range.

---

## Contract helpers (F&O)

```python
expiries = nifty.broker.expiry_list()            # list[date] of real expiries
lot      = nifty.broker.lot_size()               # contract lot size
fut_sym  = nifty.broker.future_script(expiry=0)  # Dhan future tradingsymbol

atm = nifty.broker.atm_strike()                  # ATM strike number
itm = nifty.broker.itm_strike(n=1)               # 1 strike ITM
otm = nifty.broker.otm_strike(n=1)               # 1 strike OTM

# Historical option data after expiry
nifty.broker.expired_option_data(...)
nifty.broker.exchange_time()                     # exchange clock
```

These return raw strike numbers / metadata. For a full chain object with
greeks and ATM option instruments, use
`nifty.derivatives.option_chain(...)` — see [Options Trading](08-options-trading.md).

---

## Margin & account protection

```python
# Estimate margin before you place
margin = nifty.broker.margin_calculator(
    quantity=50, transaction_type="BUY", trade_type="MIS", price=24800,
)

# Emergency: stop new orders at the broker
nifty.broker.kill_switch(action="ACTIVATE")
nifty.broker.kill_switch(action="DEACTIVATE")

# Broker-side auto-exit at profit/loss thresholds
nifty.broker.enable_pnl_based_exit(
    profit_value=5_000, loss_value=2_000,
    product_types=("INTRADAY",), enable_kill_switch=True,
)
```

`LiveRunner` already fires `kill_switch(action="ACTIVATE")` when a risk
circuit breaker trips (`RiskHaltedEvent`). See [Risk](05-risk.md).

---

## Advanced order types (Dhan-only)

The instrument facade (`.order.buy()`, `.order.bracket()`, …) covers everyday
orders on both brokers. These capabilities talk to Dhan's specialised APIs
directly.

### Super order (entry + target + stop in one call)

Prefer this for intraday brackets on live Dhan. Note: Dhan's API takes
**no** `trigger_price` — entry uses `price`; exits use target/stop.

```python
order_id = opt.broker.place_super_order(
    side="BUY", quantity=75, order_type="LIMIT", trade_type="MIS",
    price=120.0, target_price=150.0, stop_loss_price=100.0,
    trailing_jump=2.0,          # optional trailing
)

opt.broker.get_super_orders()
opt.broker.modify_super_order(
    order_id=order_id, leg_name="TARGET_LEG",
    quantity=75, order_type="LIMIT", target_price=155.0,
)
opt.broker.cancel_super_order(order_id=order_id, leg_name="ENTRY_LEG")
```

`.order.bracket(...)` on a live Dhan session routes to the same
`place_super_order` path under the hood — use whichever reads better.

### Slice order (iceberg)

```python
opt.broker.place_slice_order(
    side="BUY", quantity=1500, order_type="LIMIT",
    trade_type="MIS", price=120.0,
)
```

Use when quantity exceeds exchange freeze limits and you need the broker to
slice it.

### Forever order (GTT-style)

```python
opt.broker.place_forever_order(
    side="BUY", quantity=75, order_type="LIMIT",
    trade_type="CNC", price=100.0, trigger_price=101.0,
)
opt.broker.get_forever_orders()
opt.broker.modify_forever_order(...)
opt.broker.cancel_forever_order(order_id=...)
```

Persists until filled or cancelled — useful for levels you want watched
overnight.

### Conditional trigger (server-side alert order)

```python
opt.broker.place_conditional_trigger(
    side="BUY", quantity=75, price=120.0, trigger_price=125.0,
    order_type="LIMIT", trade_type="MIS",
    comparison_type="PRICE_WITH_VALUE", operator="ABOVE",
)
opt.broker.get_conditional_triggers()
opt.broker.get_conditional_trigger(trigger_id=...)
opt.broker.delete_conditional_trigger(trigger_id=...)
```

### Cancel everything

```python
nifty.broker.cancel_all_orders()
```

---

## Capability cheat sheet

| Capability | What it does |
|------------|--------------|
| `depth20` | 20-level order book |
| `margin_calculator` | Pre-trade margin estimate |
| `kill_switch` | Activate/deactivate broker kill switch |
| `enable_pnl_based_exit` | Broker-side PnL auto-exit |
| `expiry_list` / `lot_size` / `future_script` | F&O contract metadata |
| `atm_strike` / `itm_strike` / `otm_strike` | Strike helpers |
| `long_term_history` / `ohlc` / `start_date` / `instrument_file` | Extended market data |
| `place_super_order` (+ get/modify/cancel) | Bracket legs in one API |
| `place_slice_order` | Iceberg / freeze-qty slicing |
| `place_forever_order` (+ get/modify/cancel) | GTT-style persistent order |
| `place_conditional_trigger` (+ get/delete) | Server-side conditional |
| `cancel_all_orders` | Flatten open orders |
| `expired_option_data` / `exchange_time` | Post-expiry data / exchange clock |

---

## When to use which path

```
Need a quote / history / simple order?
  → instrument.market.*  /  instrument.order.*     (works on paper + Dhan)

Need live depth, margin, kill switch, GTT, super order?
  → instrument.broker.<capability>(...)            (Dhan only)

Need a full option chain with greeks?
  → instrument.derivatives.option_chain(...)       (see Options Trading)
```

Next: [Options Trading](08-options-trading.md) · Back: [Install & Brokers](01-install-brokers.md) · [Index](index.md)
