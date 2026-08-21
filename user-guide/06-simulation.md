# Backtest, Replay & Simulation

ntrade is **zero-parity**: write a strategy once and run it identically in
backtest, replay and live. Only the event source, the clock and the execution
target change.

```
OHLCV bars      → BacktestSimulator  → same kernel engines → fills
Recorded events → TradingSession.replay() / TradingKernel.run_replay() + ReplayClock (planned ReplayEngine) → same kernel engines → fills
Live Dhan       → DhanMarketFeedSource → same kernel engines → fills
```

This page covers the offline path: prove a strategy on paper, then gate it
before going live.

---

## 1. Backtesting — `BacktestSimulator`

```python
from ntrade import TradingSession, BacktestSimulator
from ntrade.engines.strategies import EmaCrossStrategy

# Offline history from the paper broker (no credentials)
paper = TradingSession.paper()
tcs = paper.stock("TCS")
frame = tcs.market.history()("5m", days=15, force=True).df

sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0, symbol="TCS")
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="TCS"))
result = sim.run(frame)
print(result)
```

### Reading `BacktestResult`

| Field               | Meaning                                       |
|---------------------|-----------------------------------------------|
| `final_equity`      | cash + open-position MTM at the last bar      |
| `total_return_pct`  | (final − initial) / initial × 100             |
| `n_trades`          | number of fills                               |
| `trades`            | list of fill dicts                            |
| `equity_curve`      | DataFrame `[ts, equity]`                      |
| `commissions_total` | sum of commission per fill                    |
| `statutory_total`   | Indian statutory charges (STT/SEBI/…)         |
| `max_drawdown_pct`  | deepest peak-to-trough on the equity curve    |
| `costs_total`       | commission + statutory + futures costs        |

```python
print(result.final_equity, result.total_return_pct, result.n_trades)
print("max drawdown %:", result.max_drawdown_pct)
# result.equity_curve.plot(x="ts", y="equity")   # if matplotlib is available
```

### Real Dhan history

```python
session = TradingSession.connect("dhan")
nifty = session.index("NIFTY")
frame = nifty.market.history()("5m", days=15, force=True).df

sim = BacktestSimulator(
    timeframe="5m", initial_cash=100_000.0, symbol="NIFTY", exchange="NSE",
)
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
print(sim.run(frame))
```

### Costs: slippage, commission, statutory

Indian statutory charges are on by default so backtest PnL converges on live:

```python
from ntrade import FixedSlippage, FlatCommission

sim = BacktestSimulator(
    timeframe="5m", initial_cash=100_000.0,
    slippage=FixedSlippage(0.05),        # ±₹0.05 per fill
    commission=FlatCommission(20.0),     # ₹20 flat per order
    # statutory=None  → zero-cost opt-out
)
```

### Limit orders — bar-aware fills

By default MARKET orders fill at the bar close. Pass a `FillPolicy` so LIMIT
orders only fill when a bar trades through the price (`BarAwareExecution`):

```python
from ntrade import FillPolicy

sim = BacktestSimulator(timeframe="5m", fill_policy=FillPolicy(market_on="close"))
```

---

## 2. Replay — `TradingSession.replay()` / `TradingKernel.run_replay()` & `EventStore` (planned `ReplayEngine`)

Replay feeds a **recorded event stream** (not bars) through a kernel with a
`ReplayClock`. Strategies make the same decisions as the original run.

> Note: `ReplayEngine` is planned — `ntrade/replay/` is empty (0 files). `from ntrade import ReplayEngine` will raise `ImportError`. Current replay uses `TradingSession.replay(events)` (`ntrade/kernel/trading_session.py:126`) or `TradingKernel(..., clock=ReplayClock()).run_replay(events)` (`ntrade/kernel/session.py:140`, `ntrade/kernel/clock.py:33`).

```python
from ntrade import TradingKernel, EventStore
from ntrade.kernel.clock import ReplayClock

store = EventStore("session-events.jsonl")          # append-only JSONL
kernel = TradingKernel(mode="live", store=store, timeframe="1m")
# ... run the kernel; every event is appended ...

# Later — replay only the causal market stream (current API)
market_events = store.market_events()               # Tick/Quote/Depth only
replay_kernel = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
replayed = replay_kernel.run_replay(market_events)  # returns the kernel

# Planned API (not yet implemented — will ImportError):
# from ntrade import ReplayEngine
# engine = ReplayEngine(timeframe="1m")
# replayed = engine.run(market_events)
```

Replay only `store.market_events()`. Derived events (signals, fills) are
recomputed by the kernel — never re-feed them or they double-apply.

Or via the session:

```python
session = TradingSession.replay(market_events)
session.register(session.index("NIFTY"))
session.register_strategy(EmaCrossStrategy(symbol="NIFTY"), risk={"max_quantity": 25})
session.start()
session.stop()
```

---

## 3. Synthetic ticks from 1-minute bars

`SyntheticMarketFeedSource` turns each 1-minute OHLCV bar into per-second
ticks (via `synthesize_1m_ticks`). Prices stay within high/low; open is first,
close is last, both extremes are touched, and volume sums to the bar volume.

```python
from ntrade import (
    TradingKernel, ReplayClock, SyntheticMarketFeedSource,
)
from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategies import EmaCrossStrategy

frame_1m = tcs.market.history()("1m", days=5, force=True).df

k = TradingKernel(
    mode="replay", clock=ReplayClock(), timeframe="1m", initial_cash=100_000.0,
)
k.register(Equity("TCS"))
k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="TCS"))
k.start()

src = SyntheticMarketFeedSource(k, symbol="TCS", exchange="NSE", data=frame_1m, seconds=60)
src.start()
src.join(timeout=120)
k.candle_engine.flush()
k.stop(reason="end of data")
```

`SimulatedFeedSource` is the simpler bar-level source (one Quote + one Tick
per bar) when you do not need per-second ticks.

---

## 4. From paper to live

1. **Backtest** the strategy on real or paper history (`BacktestSimulator`).
2. **Paper gate** — run the checklist script before any live money:

```bash
.venv/bin/python scripts/paper_gate_run.py --symbol NIFTY --days 15
```

It replays history through the synthetic feed and prints a
`build_paper_report` checklist (fills, final equity, max drawdown). It fails
closed when the report is unhealthy.

3. **Live runner** — `LiveRunner(kernel, feed)` starts the kernel and feed,
   polls orders and positions on an interval, and fires the broker kill switch
   when a risk breaker trips (`RiskHaltedEvent`).

```bash
.venv/bin/python scripts/live_runner_run.py
.venv/bin/python scripts/ema_cross_run.py NIFTY 15
```

See [Risk](05-risk.md) for circuit breakers and kill-switch behaviour.

---

## 5. Choosing a source

| Source                       | Input           | Events produced                          |
|------------------------------|-----------------|------------------------------------------|
| `SimulatedFeedSource`        | OHLCV frame     | 1 Quote + 1 Tick per bar                 |
| `SyntheticMarketFeedSource`  | 1m OHLCV frame  | Quote + per-second ticks (background)    |
| `DhanMarketFeedSource`       | live Dhan feed  | live Tick/Quote/Depth                    |
| `EventStore.market_events()` | recorded events | replayed Tick/Quote/Depth                |

---

Back: [Risk](05-risk.md) · [Index](index.md)
