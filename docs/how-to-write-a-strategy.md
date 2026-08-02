# ntrade — How to Write a Strategy

A strategy in ntrade is a `Strategy` subclass that **reacts to events** and
**emits signals**. It never polls prices and never places orders itself — the
kernel's engines do that. Because a strategy only sees the kernel's canonical
events, the *same class* runs identically in live trading, replay and backtest
(zero parity).

```
TickEvent/QuoteEvent ─► MarketEngine ─► QuoteUpdatedEvent
                     ─► CandleEngine ─► CandleClosedEvent ─► IndicatorEngine ─► IndicatorUpdatedEvent
                                                                                    │
                              Strategy.on_*_updated / on_candle_closed  ◄───────────┘
                                              │  emit_signal()
                                              ▼
                                   SignalGeneratedEvent
                                              │
                                   RiskEngine (screens)
                                              │
                            ┌─────────────────┴─────────────────┐
                            ▼                                 ▼
                  SignalApprovedEvent                 SignalRejectedEvent
                            │
                   OrderEngine → OrderIntentEvent → ExecutionRouter
                            │
              SimulatedExecution (paper/backtest) | BrokerExecution (live)
                            │
                   OrderFilledEvent → PortfolioEngine
                            │
              PositionUpdatedEvent + BalanceChangedEvent   ← your on_order_filled /
                                                              on_position_updated /
                                                              on_balance_changed hooks
```

---

## 1. The `Strategy` base class

Import from the engine layer:

```python
from ntrade.engines.strategy_engine import Strategy
```

Subclass it, set a `name`, override the hooks you care about, and call
`self.emit_signal(...)` to act. `self.ctx` is injected when the strategy is
registered — it gives you access to the session state.

Available hooks (all optional, event-typed):

| Hook                    | Event received              | Fires when                                  |
|-------------------------|-----------------------------|---------------------------------------------|
| `on_tick`               | `TickEvent`                 | every trade/quote/depth print               |
| `on_quote_updated`      | `QuoteUpdatedEvent`         | MarketEngine projected a quote into a symbol|
| `on_candle_closed`      | `CandleClosedEvent`         | a `timeframe` candle completes              |
| `on_indicator_updated`  | `IndicatorUpdatedEvent`     | IndicatorEngine computed a fresh bundle     |
| `on_position_updated`   | `PositionUpdatedEvent`      | a fill changed a position                   |
| `on_order_filled`       | `OrderFilledEvent`          | an order (fully) filled                     |
| `on_balance_changed`    | `BalanceChangedEvent`       | cash balance changed                        |

Useful context (`self.ctx`):

```python
self.ctx.now()                              # ALWAYS this, never datetime.now()
self.ctx.instrument("NIFTY")                # the registered Instrument
self.ctx.portfolio.position("NIFTY")        # Position | None
self.ctx.account.balance                    # float
self.ctx.mode                               # "live" | "replay" | "backtest"
```

---

## 2. A minimal strategy

Here is a strategy that buys the first time RSI(14) dips below 30 and sells the
first time it climbs above 70, using the indicator bundle that the
`IndicatorEngine` projects onto the instrument after every candle close:

```python
from ntrade.engines.strategy_engine import Strategy


class RsiReversal(Strategy):
    name = "rsi_reversal"

    def __init__(self, quantity: int = 10, symbol: str = "NIFTY",
                 lower: float = 30.0, upper: float = 70.0):
        super().__init__()
        self.quantity = int(quantity)
        self.symbol = symbol
        self.lower = lower
        self.upper = upper

    def on_candle_closed(self, event) -> None:
        if event.symbol != self.symbol:
            return
        bundle = self.ctx.instrument(event.symbol)._indicators
        rsi = bundle.get("rsi_14")
        if rsi is None:
            return                        # warm-up: not enough candles yet
        position = self.ctx.portfolio.position(event.symbol)
        qty = position.quantity if position is not None else 0

        if rsi < self.lower and qty <= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=event.close)
        elif rsi > self.upper and qty >= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=self.quantity, price=event.close)
```

The indicator bundle keys are `rsi_14`, `atr_14`, `vwap`, `avg_volume`,
`stx_10_3`, `ema_9`, `ema_21` (all configurable via the `IndicatorEngine`
params). If you need an indicator the engine does not emit, read history
directly through the instrument:

```python
inst = self.ctx.instrument(self.symbol)
series = inst.market.history()("5m")       # HistoricalSeries
df = series.df                             # pandas DataFrame (OHLCV)
```

---

## 3. `emit_signal` — the contract

`emit_signal` publishes a `SignalGeneratedEvent` that the RiskEngine screens
before it can become an order. It is the **only** way a strategy acts:

```python
self.emit_signal(symbol="NIFTY", exchange="NSE", side="BUY",
                 quantity=10, price=24800.0, my_note="golden cross")
```

Semantics to know:

- `price=0.0` (or omitted) → the OMS submits a **MARKET** order. A nonzero
  `price` → a **LIMIT** order at that price. (See `OrderEngine`: `order_type =
  "LIMIT" if signal.price else "MARKET"`.)
- `exchange` defaults to `"NSE"`.
- Extra keyword arguments land in `metadata` on the event — handy for logging,
  and they propagate to position metadata.
- The signal's `strategy` field is set to `self.name` automatically. It is how
  per-strategy risk engines and position accounting attribute the trade.

`emit_signal` uses `self.ctx.now()` for the timestamp, which is the whole point
of zero parity: in replay/backtest the clock is deterministic, so the same
strategy on the same data makes the same decisions every time.

---

## 4. What happens after you emit — risk screening

Signals do not become orders automatically. `RiskEngine` subscribes to
`SignalGeneratedEvent` and either publishes `SignalApprovedEvent` or
`SignalRejectedEvent`. Only approved signals reach the OrderEngine.

The kernel builds a RiskEngine with **no limits configured** — by default
every signal passes. You set the limits at registration (below). Available
knobs:

| Kwarg                  | Rejects when                                        |
|------------------------|-----------------------------------------------------|
| `max_quantity`         | signal quantity > cap                               |
| `max_notional`         | price × quantity > cap                              |
| `max_positions`        | open position count ≥ cap                           |
| `allowlist`            | symbol not in the allowed set                       |
| `price_deviation_pct`  | price deviates > X% from the last traded price      |
| `max_daily_loss`       | session equity falls more than X from session start |
| `max_drawdown_pct`     | equity drawdown from peak > X%                      |

Circuit breakers (`max_daily_loss`, `max_drawdown_pct`) halt all trading until
`resume()`; while halted every signal is rejected with `risk halted: <reason>`.

You can watch the screening outcome in the kernel's event history:

```python
from ntrade import SignalApprovedEvent, SignalRejectedEvent

k = ...  # your kernel
approved = [e for e in k.bus.history if isinstance(e, SignalApprovedEvent)]
rejected = [e for e in k.bus.history if isinstance(e, SignalRejectedEvent)]
print(len(approved), "approved", len(rejected), "rejected")
```

---

## 5. Registering your strategy

There are two registration paths; pick per use case.

### (a) Via `TradingSession` — per-strategy risk, named

The session's `register_strategy` goes through `StrategyRunner`, which gives
each strategy a **unique name** and its **own scoped RiskEngine**. Risk kwargs
apply to this strategy only:

```python
session = TradingSession.paper(initial_cash=100_000.0)
session.register(session.index("NIFTY"))
handle = session.register_strategy(
    RsiReversal(quantity=10, symbol="NIFTY"),
    risk={
        "max_quantity": 25,
        "max_notional": 1_000_000,
        "max_positions": 2,
        "allowlist": {"NIFTY"},
    },
)
session.start()
# ... feed market data (e.g. a SimulatedFeedSource / SyntheticMarketFeedSource
# in paper mode, or the live Dhan feed) ...
session.stop()
```

`register_strategy` returns the (possibly de-duplicated) name, e.g. `"rsi_reversal"`
or `"rsi_reversal#2"` if the name is taken. The runner supports hot
enable/disable and status reporting:

```python
session.runner.disable(handle)
session.runner.enable(handle)
session.runner.status()          # per-strategy limits + approve/reject counts
```

### (b) Directly on the kernel — shared global risk

For lower-level control (e.g. inside a `BacktestSimulator` or a raw
`TradingKernel`), register straight onto the strategy engine. These use the
kernel's single global RiskEngine:

```python
from ntrade import TradingKernel, ReplayClock
from ntrade.engines.strategies import EmaCrossStrategy   # built-in example

k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="5m",
                  initial_cash=100_000.0)
k.register(...)                  # instruments
k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
k.start()
```

> Note the kernel's `register_strategy` signature is `(strategy)` only — it does
> **not** accept a `risk=` dict. Use the session/runner path when you want
> scoped risk. `BacktestSimulator.register_strategy(strategy)` also takes just
> the strategy; configure costs via the simulator constructor instead.

---

## 6. Running identically in live, replay and backtest

Because the strategy only reacts to canonical events, the same object works in
all three modes — the differences are purely the event source, the clock and
the execution target:

| Mode      | Event source                          | Clock         | Execution target    |
|-----------|---------------------------------------|---------------|---------------------|
| backtest  | `BacktestSimulator` over OHLCV bars   | `SimulationClock` | `SimulatedExecution` (+ costs) |
| replay    | `ReplayEngine` / `store.market_events()` | `ReplayClock` | `SimulatedExecution`      |
| live      | Dhan feed (`DhanMarketFeedSource`)    | `LiveClock`   | `BrokerExecution`    |

The same `RsiReversal` above, plugged into the backtest path unchanged:

```python
import math
import pandas as pd
from ntrade import BacktestSimulator
from ntrade.engines.strategies import EmaCrossStrategy   # or your strategy

close = [100 + 10 * math.sin(i / 8) for i in range(120)]
n = len(close)
frame = pd.DataFrame({
    "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min"),
    "open": [100.0] + close[:-1],
    "high": [c * 1.005 for c in close],
    "low": [c * 0.995 for c in close],
    "close": close,
    "volume": [1000] * n,
})

sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0, symbol="NIFTY")
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
result = sim.run(frame)
print(result)      # BacktestResult: final_equity, n_trades, max_drawdown_pct, ...
```

And through a `TradingSession` replay:

```python
session = TradingSession.replay(market_events)     # events from an EventStore
session.register(session.index("NIFTY"))
session.register_strategy(RsiReversal(symbol="NIFTY"),
                          risk={"max_quantity": 25})
session.start()
session.stop()
```

---

## 7. Good practices (learned from the built-in `EmaCrossStrategy`)

- **Read indicator bundles, don't recompute.** `bundle = inst._indicators` is
  already populated by the `IndicatorEngine` on every candle close. Check for
  `None` on keys to skip warm-up gracefully.
- **Be position-aware.** Check `self.ctx.portfolio.position(symbol)` before
  emitting so you do not stack positions: BUY only when flat/short, SELL only
  when flat/long (the `EmaCrossStrategy` reverses instead of stacking).
- **Never call `datetime.now()`.** Use `self.ctx.now()` — the only way to stay
  deterministic in replay/backtest.
- **Return early per symbol.** A strategy registered without a `symbol` runs on
  *every* candle; guard with `if event.symbol != self.symbol: return`.
- **Strategy errors are swallowed.** The dispatch loop catches per-strategy
  exceptions so one broken hook never kills the kernel — which means **your
  strategy's exceptions are silent**. Write defensively, and unit-test hooks
  with hand-built events:

```python
from ntrade.events.market import CandleClosedEvent

strat = RsiReversal(symbol="NIFTY")
# give the strategy a ctx by registering it on a kernel, then feed events
k.register_strategy(strat)
k.publish(CandleClosedEvent(symbol="NIFTY", exchange="NSE", timeframe="5m",
                            open=100, high=101, low=99, close=100.5, volume=1000,
                            ts=k.clock.now()))
assert ...   # assert the strategy's reaction
```

Note the `ts=` — every event carries a timestamp from the clock. And the
instrument must be registered (`k.register(...)`) for `ctx.instrument(symbol)`
to resolve in the hook.

---

## 8. In a Jupyter notebook

```python
# Cell 0 — environment setup
import sys
sys.path.insert(0, "/path/to/nTrade")
%load_ext autoreload
%autoreload 2

from ntrade import BacktestSimulator, TradingSession
from ntrade.engines.strategy_engine import Strategy
```

```python
# Cell 1 — define your strategy (edit + re-run this cell, autoreload picks it up)
class RsiReversal(Strategy):
    name = "rsi_reversal"

    def __init__(self, quantity=10, symbol="NIFTY", lower=30.0, upper=70.0):
        super().__init__()
        self.quantity, self.symbol = quantity, symbol
        self.lower, self.upper = lower, upper

    def on_candle_closed(self, event):
        if event.symbol != self.symbol:
            return
        rsi = self.ctx.instrument(event.symbol)._indicators.get("rsi_14")
        if rsi is None:
            return
        pos = self.ctx.portfolio.position(event.symbol)
        qty = pos.quantity if pos else 0
        if rsi < self.lower and qty <= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=event.close)
        elif rsi > self.upper and qty >= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=self.quantity, price=event.close)
```

```python
# Cell 2 — backtest it
paper = TradingSession.paper(initial_cash=100_000.0)
tcs = paper.stock("TCS")
frame = tcs.market.history()("5m", days=15, force=True).df

sim = BacktestSimulator(timeframe="5m", symbol="TCS", initial_cash=100_000.0)
sim.register_strategy(RsiReversal(symbol="TCS"))
result = sim.run(frame)
print(result)

# inspect the event chain
from ntrade import SignalApprovedEvent, SignalRejectedEvent, OrderFilledEvent
print("approved:", sum(1 for e in sim.kernel.bus.history if isinstance(e, SignalApprovedEvent)))
print("rejected:", sum(1 for e in sim.kernel.bus.history if isinstance(e, SignalRejectedEvent)))
print("fills:   ", sum(1 for e in sim.kernel.bus.history if isinstance(e, OrderFilledEvent)))
```

```python
# Cell 3 — tune the risk knobs and re-run
sim2 = BacktestSimulator(timeframe="5m", symbol="TCS", initial_cash=100_000.0)
sim2.register_strategy(RsiReversal(symbol="TCS", lower=35, upper=65))
print(sim2.run(frame))
```

Notebook tips:

- `%autoreload` re-imports your strategy class when you edit Cell 1, but a fresh
  `BacktestSimulator` per run is still the cleanest way to isolate state.
- The indicator bundle needs warm-up candles (`IndicatorEngine` computes from
  ~10 rows); the first bars of a short backtest produce no signals.
- To see *why* signals were rejected, print `e.reason` on each
  `SignalRejectedEvent`.
