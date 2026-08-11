# Options Trading

Options in ntrade are first-class instruments. You fetch a chain from an
underlying, navigate ATM/ITM/OTM, read greeks, and place orders on the option
object — the same way you would on a stock.

Live chains need Dhan. Paper seeds a deterministic chain for offline practice.

```python
from ntrade import TradingSession

# Live
session = TradingSession.connect("dhan")
nifty = session.index("NIFTY")
nifty.market.refresh()

# Or paper (offline)
# session = TradingSession.paper()
# nifty = session.index("NIFTY")
# nifty.market.refresh()
```

---

## 1. Fetch a chain

```python
chain = nifty.derivatives.option_chain(expiry=0, num_strikes=10)

# Or via the session helper
chain = session.chain(nifty, expiry=0, num_strikes=10)

# Or build a single option directly
from datetime import date
opt = session.option(nifty, strike=25000, expiry=date(2026, 8, 27), option_type="CE")
```

- `expiry=0` = nearest expiry, `1` = next, and so on.
- `num_strikes` controls how many strikes around ATM to pull.
- If the broker falls back to another expiry week, check
  `chain.expiry_index_used` and the real contract dates via
  `nifty.broker.expiry_list()`.

```python
print(len(chain), "options")
print("nearest:", chain.nearest_expiry)
print("real expiries:", nifty.broker.expiry_list())   # list[date]
print("strikes:", chain.strikes[:5], "...")
```

---

## 2. Navigate the chain

```python
chain.calls                         # all call Options
chain.puts                          # all put Options
chain.atm                           # at-the-money Option (CE preferred)
chain.itm                           # in-the-money Options
chain.otm                           # out-of-the-money Options

# Exact strike lookup (O(1))
ce = chain.at_strike(25000, "CE")
pe = chain.at_strike(25000, "PE")

# Expiry views
near = chain.expiry(0)              # Expiry object for nearest
near.atm()                          # OptionPair (CE+PE) at ATM
near.otm(2)                         # 2 OTM options
near.itm(2)
near.pcr()
```

Each element is a real `Option` instrument — it has `.market`, `.order`,
`.analytics`, `.stream`, and (on Dhan) `.broker` capabilities.

---

## 3. Greeks, IV, max pain, PCR

Greeks default to a zero-filled object so `chain.atm.greeks.delta` never
raises when data is missing.

```python
atm = chain.atm
print(atm.symbol, atm.strike, atm.option_type)
print("ltp:", atm.market.ltp(), "iv:", atm.iv)
print("delta:", atm.greeks.delta, "gamma:", atm.greeks.gamma)
print("theta:", atm.greeks.theta, "vega:", atm.greeks.vega)

print("PCR:", chain.pcr())                 # put/call OI ratio
print("max pain:", chain.max_pain())       # strike with max seller payout

table = chain.greeks()                     # GreeksTable (DataFrame-like)
surface = chain.iv_surface()               # IVSurface across strikes/expiries
```

---

## 4. Place an option order

Same facade as equities. Prefer paper first.

```python
atm = chain.atm

# Simple limit buy of 1 lot (on Dhan, prefer nifty.broker.lot_size())
lot = 75
order = atm.order.buy(lot, price=atm.market.ltp() or 0, order_type="LIMIT")

# Market
order = atm.order.market("BUY", lot)

# Bracket / cover via facade (on Dhan, BRACKET routes to place_super_order)
order = atm.order.bracket(
    "BUY", lot,
    price=120.0, target_price=150.0, stop_loss_price=100.0,
)

# Or call the Dhan super-order capability directly
order_id = atm.broker.place_super_order(
    side="BUY", quantity=lot, order_type="LIMIT", trade_type="MIS",
    price=120.0, target_price=150.0, stop_loss_price=100.0,
)
```

> **Warning:** Live option orders hit the real market. Size in lots, check
> margin with `atm.broker.margin_calculator(...)`, and test the path on paper
> or via [Simulation](06-simulation.md) first.

Cancel / modify:

```python
order.cancel()
order.modify(price=118.0)
```

---

## 5. Typical option flows

### A. Morning chain scan → ATM trade

```python
nifty.market.refresh()
chain = nifty.derivatives.option_chain(expiry=0, num_strikes=5)
print("ATM", chain.atm.symbol, "delta", chain.atm.greeks.delta, "PCR", chain.pcr())

# Hand off to a strategy or place manually
atm = chain.atm
atm.order.buy(75, price=atm.market.ltp() or 0)
```

### B. Pick ITM/OTM by moneyness

```python
itm_calls = [o for o in chain.itm if o.option_type == "CE"]
otm_puts  = [o for o in chain.otm if o.option_type == "PE"]
```

Or use Dhan strike helpers when you only need the number:

```python
strike = nifty.broker.otm_strike(n=2)
opt = chain.at_strike(strike, "CE")
```

### C. Subscribe the whole chain for live ticks

```python
chain.subscribe()                   # subscribe every option in the chain
atm.stream.on_tick(lambda t: print(atm.symbol, t.price))
```

### D. Multi-expiry

```python
for exp in chain.expiries():
    print(exp.date, "ATM pair", exp.atm(), "PCR", exp.pcr())
```

### E. Build from session factory (known strike/expiry)

```python
from datetime import date
ce = session.option(nifty, strike=25000, expiry=date(2026, 8, 27), option_type="CE")
ce.market.refresh()
ce.order.market("BUY", 75)
```

---

## 6. Futures alongside options

```python
from datetime import date

fut = session.future(nifty, expiry=date(2026, 8, 27))
fut.market.refresh()
# On Dhan: resolve the tradingsymbol / lot
# nifty.broker.future_script(expiry=0)
# nifty.broker.lot_size()
```

---

## Paper vs live for options

| Step | Paper | Dhan |
|------|-------|------|
| `option_chain(...)` | Seeded deterministic chain | Live chain from broker |
| Greeks / PCR / max pain | From seeded quotes | From live quotes |
| `.order.*` | Simulated fills | Real OMS |
| `.broker.place_super_order` / strike helpers | `AttributeError` | Live |

Develop the navigation and signal logic on paper; switch the session to
`connect("dhan")` when you need real chains and live placement.

---

Next: [Trading Flows](09-trading-flows.md) · Related: [Broker Capabilities](07-broker-capabilities.md) · [Index](index.md)
