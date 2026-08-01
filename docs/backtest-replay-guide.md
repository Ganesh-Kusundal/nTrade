# ntrade — Backtesting, Replay & Simulation Guide

ntrade is **zero-parity by construction**: a strategy is written once against
the kernel's canonical events and runs identically in live trading
(`BrokerExecution`), replay (`ReplayEngine`) and backtest
(`BacktestSimulator`). Only the event source, the execution target and the
clock differ.

```
OHLCV frame ─► BacktestSimulator ─► TickEvent/QuoteEvent ─► EventBus
recorded    ─► ReplayEngine      ─► TickEvent/QuoteEvent ─► EventBus
Dhan live   ─► DhanMarketFeedSource ─► TickEvent          ─► EventBus
                                            │
        MarketEngine → CandleEngine → IndicatorEngine → Strategy
        → RiskEngine → OrderEngine → ExecutionRouter → Fills → PortfolioEngine
```

This guide covers the two offline flows: **backtest** (historical bars) and
**replay** (recorded events), plus the underlying `TradingKernel` when you
want more control.

---

## 1. Backtesting — `BacktestSimulator`

`BacktestSimulator` runs the standard kernel over an OHLCV frame and returns a
`BacktestResult`. It is the closest thing to a one-liner in the framework:

```python
import math
import pandas as pd
from ntrade import BacktestSimulator
from ntrade.engines.strategies import EmaCrossStrategy

# An OHLCV frame with timestamp/open/high/low/close/volume columns
close = [100 + 10 * math.sin(i / 8) for i in range(120)]   # oscillating path → crosses
n = len(close)
df = pd.DataFrame({
    "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min"),
    "open":  [100.0] + close[:-1],
    "high":  [c * 1.005 for c in close],
    "low":   [c * 0.995 for c in close],
    "close": close,
    "volume": [1000] * n,
})

sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0, symbol="NIFTY")
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
result = sim.run(df)
```

`BacktestResult` fields:

| Field                | Meaning                                        |
|----------------------|------------------------------------------------|
| `final_equity`       | balance + open-position MTM at the last bar    |
| `total_return_pct`   | (final − initial) / initial × 100              |
| `n_trades`           | number of fills                                |
| `trades`             | list of fill dicts (side/qty/price/costs/ts)   |
| `equity_curve`       | DataFrame[ts, equity]                          |
| `commissions_total`  | sum of commission per fill                     |
| `statutory_total`    | sum of Indian statutory charges (STT/SEBI/…)   |
| `max_drawdown_pct`   | deepest peak-to-trough on the equity curve     |
| `costs_total`        | commission + statutory + futures costs         |

```python
print(result)
print(result.final_equity, result.total_return_pct, result.n_trades)
result.equity_curve.plot(x="ts", y="equity")
```

### Real historical data instead of a toy frame

Fetch bars through the broker (live Dhan, or seed a paper broker), then run:

```python
from ntrade import TradingSession

session = TradingSession.connect("dhan")
nifty = session.index("NIFTY")
frame = nifty.market.history()("5m", days=15, force=True).df   # pandas DataFrame

sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0,
                        symbol="NIFTY", exchange="NSE")
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
result = sim.run(frame)
print(result)
```

Fully offline (no credentials):

```python
from ntrade import TradingSession, BacktestSimulator, EmaCrossStrategy
from ntrade.domain.instruments.cash import Equity

paper = TradingSession.paper()
tcs = paper.stock("TCS")
frame = tcs.market.history()("5m", days=10, force=True).df   # PaperBroker seeds it

sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0,
                        symbol="TCS", instrument=Equity("TCS"))
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="TCS"))
print(sim.run(frame))
```

### Costs: slippage, commission, statutory charges

Indian statutory charges (STT, exchange txn, SEBI, GST, stamp duty) are on by
default so backtest PnL converges on live. Configure or opt out explicitly:

```python
from ntrade import (
    FixedSlippage, PercentageSlippage,
    FlatCommission, PercentageCommission,
    IndianStatutoryCosts,
)
from ntrade.execution.costs import FuturesCarryCosts

sim = BacktestSimulator(
    timeframe="5m", initial_cash=100_000.0,
    slippage=FixedSlippage(0.05),                 # ±₹0.05 per fill
    commission=FlatCommission(20.0),              # ₹20 flat per order
    # statutory=None  → zero-cost opt-out (default is the real schedule)
    # statutory=IndianStatutoryCosts(product="futures", delivery=True)
    futures_costs=FuturesCarryCosts(risk_free=0.065, roll_pct=0.0002),  # F&O only
)
```

`FuturesCarryCosts` (daily carry + expiry roll) only applies when the backtest
instrument is a `Future`; on equity backtests it is a no-op.

### Limit orders — bar-aware fills

By default MARKET orders fill at the bar close. For LIMIT orders that should
only fill when a bar actually trades through the price, pass a `FillPolicy`:

```python
from ntrade import FillPolicy

# market_on="open" fills market orders at the bar open; "close" is default
sim = BacktestSimulator(timeframe="5m", fill_policy=FillPolicy(market_on="close"))
```

---

## 2. Replay — `ReplayEngine` & `EventStore`

Replay feeds a **recorded event stream** (not bars) back through a kernel with
a `ReplayClock`, so the clock follows event timestamps and strategies make
identical decisions to the original run.

### Record events live, replay them later

```python
from ntrade import TradingKernel, EventStore, ReplayEngine

store = EventStore("session-events.jsonl")          # append-only JSONL
kernel = TradingKernel(mode="live", store=store, timeframe="1m")
# ... run the kernel; every event is appended to the store ...

# Later — deterministic replay of just the market-data events
market_events = store.market_events()               # Tick/Quote/Depth only
engine = ReplayEngine(timeframe="1m")
replayed = engine.run(market_events)                # returns the kernel
```

Replay only `store.market_events()` — derived events (signals, fills) are
recomputed by the kernel and must never be re-fed, or they would double-apply.

`EventStore` also powers crash recovery (`ResilientKernel.recover()`), audits
(`store.events(OrderFilledEvent)`), and order-lifecycle reconstruction
(`store.open_order_deltas()`).

---

## 3. Lower level — `TradingKernel` + feed sources

For full control (multiple instruments, custom sources, direct event access),
drive the kernel directly. This is exactly what `scripts/ema_cross_run.py`
does with real Dhan data:

```python
from ntrade import (
    TradingKernel, ReplayClock, SimulatedFeedSource,
    SyntheticMarketFeedSource,
)
from ntrade.domain.instruments.cash import Index
from ntrade.engines.strategies import EmaCrossStrategy

k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="5m",
                  initial_cash=100_000.0)
k.register(Index("NIFTY"))
k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
k.start()

# 1) SimulatedFeedSource — publish each bar as QuoteEvent + close TickEvent
src = SimulatedFeedSource(k, symbol="NIFTY", exchange="NSE", data=frame)
src.start()

# 2) SyntheticMarketFeedSource — extrapolate each 1m bar into per-second ticks
# src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=frame_1m, seconds=60)
# src.start(); src.join(timeout=120)

k.candle_engine.flush()          # close the final partial candle
k.stop(reason="end of data")

# Inspect what happened
from ntrade import OrderFilledEvent
fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
for f in fills:
    print(f.ts, f.side, f.quantity, f.fill_price, f.commission, f.statutory)

pos = k.ctx.portfolio.position("NIFTY")
print("position:", pos.quantity if pos else 0, "| balance:", k.ctx.account.balance)
```

Key kernel surface:

- `k.register(instrument)` / `k.register_strategy(strategy)` / `k.start()` /
  `k.stop(reason=...)`
- `k.bus.publish(event)` / `k.bus.history` — every event the bus saw
- `k.ctx.account.balance`, `k.ctx.portfolio.positions`
- `k.publish()` for hand-built canonical events (`QuoteEvent`, `TickEvent`…)

### Choosing a source

| Source                          | Input             | Events produced              |
|---------------------------------|-------------------|------------------------------|
| `SimulatedFeedSource`           | OHLCV frame       | 1 QuoteEvent + 1 TickEvent per bar |
| `SyntheticMarketFeedSource`     | 1m OHLCV frame    | 1 QuoteEvent + N per-second TickEvents per bar (background thread) |
| `DhanMarketFeedSource`          | live Dhan feed    | live Tick/Quote/Depth events |
| `EventStore.market_events()`    | recorded events   | replayed Tick/Quote/Depth    |

---

## 4. Ready-made strategy + paper gate

- `EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol=...)` — canonical EMA
  crossover, always-in-market, reverses instead of stacking. Ready to use in
  live, replay and backtest.
- `scripts/ema_cross_run.py` — full pipeline on real Dhan data (fetch → kernel
  → strategy → fills → report). Run: `.venv/bin/python scripts/ema_cross_run.py NIFTY 15`
- `scripts/paper_gate_run.py` — paper→live gate: replay real history through
  the synthetic feed and print a `build_paper_report` checklist (fills, final
  equity, max drawdown). Fails closed when the report is unhealthy. Run:
  `.venv/bin/python scripts/paper_gate_run.py --symbol NIFTY --days 15`

---

## 5. In a Jupyter notebook

```python
# Cell 0 — environment setup (skip if `pip install -e .` is done)
import sys
sys.path.insert(0, "/path/to/nTrade")
%load_ext autoreload
%autoreload 2

import pandas as pd
from ntrade import BacktestSimulator, TradingSession
from ntrade.engines.strategies import EmaCrossStrategy
```

```python
# Cell 1 — fetch real history (paper: offline; dhan: needs .env)
paper = TradingSession.paper()
tcs = paper.stock("TCS")
frame = tcs.market.history()("5m", days=15, force=True).df
frame.tail()
```

```python
# Cell 2 — backtest
sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0,
                        symbol="TCS", slippage=0.0, commission=0.0)
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="TCS"))
result = sim.run(frame)

print(result)                        # one-line summary
print("return:", result.total_return_pct, "%  |  trades:", result.n_trades,
      "| max_dd:", result.max_drawdown_pct, "%")
```

```python
# Cell 3 — equity curve + trades in the notebook (needs matplotlib)
result.equity_curve.set_index("ts")["equity"].plot(title=f"{tcs.symbol} backtest")
result.trades[:5]
```

```python
# Cell 4 — replay the same data as events (kernel-level control)
from ntrade import TradingKernel, ReplayClock, SimulatedFeedSource
from ntrade.domain.instruments.cash import Equity

k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="5m",
                  initial_cash=100_000.0)
k.register(Equity("TCS"))
k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="TCS"))
k.start()
SimulatedFeedSource(k, symbol="TCS", exchange="NSE", data=frame).start()
k.candle_engine.flush()
k.stop(reason="done")

from ntrade import OrderFilledEvent
fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
print(f"{len(fills)} fills; final balance {k.ctx.account.balance:,.2f}")
```

Notebook tips:

- `BacktestSimulator.run(df)` is synchronous — it returns when the last bar is
  processed. There is no background thread to join.
- `SyntheticMarketFeedSource` runs in a background thread — call
  `src.join(timeout=...)` before `k.stop()`.
- A fresh `TradingKernel` per experiment keeps state isolated; reuse the same
  `frame` across runs to compare strategy parameters.
- `result.equity_curve` is a plain DataFrame — feed it to any plotting lib.
