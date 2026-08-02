# Install & Brokers

ntrade hides broker transport behind adapters. You talk to market objects; the
broker (Dhan live or paper offline) stays behind the session. Swapping brokers
does not change your trading code.

```python
from ntrade import TradingSession

session = TradingSession.connect("dhan")   # live Dhan
nifty   = session.index("NIFTY")
nifty.market.refresh()

paper = TradingSession.paper()             # offline, no credentials
tcs   = paper.stock("TCS")
```

---

## 1. Install

From the repo root:

```bash
pip install -e .            # pandas, numpy, Dhan-Tradehull
```

For a **live Dhan** session, put credentials in a `.env` file in the working
directory (the auth layer reads it). Paper trading needs **no** credentials.

```python
from ntrade import TradingSession
```

---

## 2. Choosing a broker

`BrokerRegistry` maps a name to a factory. Dhan auto-registers only if
`Dhan-Tradehull` is installed.

```python
from ntrade import BrokerRegistry

BrokerRegistry.available()          # typically ['dhan', 'paper']

broker = BrokerRegistry.get("paper")
broker = BrokerRegistry.get("dhan", env_path=".env")
```

| Broker | Name     | Needs credentials | Use for                                      |
|--------|----------|-------------------|----------------------------------------------|
| Dhan   | `"dhan"` | yes (`.env`)      | live quotes, history, orders, option chains  |
| Paper  | `"paper"`| no                | tests, backtests, replays — deterministic    |

---

## 3. The session — preferred entry point

`TradingSession` is the one-stop API. Three constructors cover the three modes:

```python
# Live — named broker, `.env` credentials
session = TradingSession.connect("dhan")

# Paper — deterministic, offline, seeded RNG
session = TradingSession.paper(initial_cash=100_000.0)

# Replay — feed previously recorded events through the kernel
session = TradingSession.replay(events)
```

Everything after this point is mode-independent: the same calls work live and
on paper. See [Core Journey](02-core-journey.md) for quotes, history and orders.

---

## 4. Jupyter notebook setup

If the package is not installed, put the repo on the path in the first cell:

```python
# Cell 0 — environment setup
import sys
sys.path.insert(0, "/path/to/nTrade")     # repo root
%load_ext autoreload
%autoreload 2

from ntrade import TradingSession
```

```python
# Cell 1 — paper session (safe to play with)
session = TradingSession.paper(initial_cash=100_000.0)
tcs = session.stock("TCS")
tcs.market.refresh()
print("LTP:", tcs.market.ltp())
```

```python
# Cell 2 — live session (needs .env)
session = TradingSession.connect("dhan")
nifty = session.index("NIFTY")
nifty.market.refresh()
print("NIFTY:", nifty.market.ltp())
```

Tips:

- Prefer `session.balance()` and `session.positions()` for read-only live checks.
- Keep **one** session per notebook; do not create a fresh `TradingSession`
  inside a loop.
- Restart the kernel after changing instrument code (symbol caches are
  per-process).

---

## 5. Legacy `Market` facade

`Market` predates `TradingSession` and still works. Prefer `TradingSession` for
new code.

```python
from ntrade import Market

m = Market(broker="dhan")        # live
m = Market(broker="paper")       # offline
nifty = m.index("NIFTY")
nifty.market.refresh()
m.balance()
```

---

Next: [Core Journey](02-core-journey.md) · Back: [Index](index.md)
