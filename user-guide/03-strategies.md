# Writing Strategies

A strategy watches market events and emits buy/sell signals. It never places
orders itself — the kernel screens each signal for risk, then turns approved
signals into orders. Write the strategy once; the same class runs in live,
replay and backtest.

```
Candle closes → indicators update → strategy emits signal
                                 → risk screen
                                 → order → fill → portfolio update
```

---

## 1. Ready-made: EMA crossover

```python
from ntrade import TradingSession, BacktestSimulator
from ntrade.engines.strategies import EmaCrossStrategy

# Always-in-market EMA cross: BUY on golden cross, SELL on death cross.
# Reverses instead of stacking. Reads ema_9 / ema_21 from the indicator bundle.
strategy = EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY")
```

Use it in a backtest (see [Simulation](06-simulation.md)) or register it on a
session (below).

---

## 2. A minimal custom strategy

Subclass `Strategy`, set a `name`, override the hooks you care about, and call
`self.emit_signal(...)`.

```python
from ntrade import Strategy


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
            self.emit_signal(
                symbol=event.symbol, exchange=event.exchange,
                side="BUY", quantity=self.quantity, price=event.close,
            )
        elif rsi > self.upper and qty >= 0:
            self.emit_signal(
                symbol=event.symbol, exchange=event.exchange,
                side="SELL", quantity=self.quantity, price=event.close,
            )
```

### Available hooks (all optional)

| Hook                   | Fires when                                      |
|------------------------|-------------------------------------------------|
| `on_tick`              | every trade/quote/depth print                   |
| `on_quote_updated`     | a quote was projected into a symbol             |
| `on_candle_closed`     | a candle completes                              |
| `on_indicator_updated` | a fresh indicator bundle is ready               |
| `on_position_updated`  | a fill changed a position                       |
| `on_order_filled`      | an order filled                                 |
| `on_balance_changed`   | cash balance changed                            |

### Useful context (`self.ctx`)

```python
self.ctx.now()                              # ALWAYS this, never datetime.now()
self.ctx.instrument("NIFTY")                # the registered Instrument
self.ctx.portfolio.position("NIFTY")        # Position | None
self.ctx.account.balance                    # float
self.ctx.mode                               # "live" | "replay" | "backtest"
```

Indicator bundle keys commonly include `rsi_14`, `atr_14`, `vwap`,
`avg_volume`, `ema_9`, `ema_21`. Check for `None` to skip warm-up gracefully.

---

## 3. `emit_signal` — the only way a strategy acts

```python
self.emit_signal(
    symbol="NIFTY", exchange="NSE", side="BUY",
    quantity=10, price=24800.0, my_note="golden cross",
)
```

- `price=0.0` (or omitted) → **MARKET** order. Nonzero `price` → **LIMIT**.
- `exchange` defaults to `"NSE"`.
- Extra keywords land in event `metadata` (logging / attribution).
- The signal's `strategy` field is set to `self.name` automatically.

---

## 4. Registering your strategy

### Via `TradingSession` — preferred (per-strategy risk)

`register_strategy` goes through `StrategyRunner`, which gives each strategy a
unique name and its **own** risk engine. Risk kwargs apply to this strategy only:

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
# ... feed market data ...
session.stop()
```

Hot control:

```python
session.runner.disable(handle)
session.runner.enable(handle)
session.runner.status()          # limits + approve/reject counts
session.runner.remove(handle)
session.runner.release()         # restore the global RiskEngine
```

While the runner owns strategies, the kernel's global RiskEngine is paused so
a signal is never screened twice.

### Directly on the kernel — shared global risk

```python
from ntrade import TradingKernel, ReplayClock
from ntrade.engines.strategies import EmaCrossStrategy

k = TradingKernel(
    mode="replay", clock=ReplayClock(), timeframe="5m",
    initial_cash=100_000.0,
)
k.register(...)
k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
k.start()
```

The kernel path does **not** accept a `risk=` dict — use the session/runner
path when you want scoped risk. Circuit breakers are covered in
[Risk](05-risk.md).

---

## 5. Same strategy in live, replay and backtest

| Mode     | Event source                       | Clock             | Execution              |
|----------|------------------------------------|-------------------|------------------------|
| backtest | `BacktestSimulator` over OHLCV     | `SimulationClock` | `SimulatedExecution`   |
| replay   | `ReplayEngine` / recorded events   | `ReplayClock`     | `SimulatedExecution`   |
| live     | `DhanMarketFeedSource`             | `LiveClock`       | `BrokerExecution`      |

```python
from ntrade import BacktestSimulator
from ntrade.engines.strategies import EmaCrossStrategy

sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0, symbol="NIFTY")
sim.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"))
result = sim.run(frame)   # pandas OHLCV DataFrame
print(result)
```

---

## 6. Good practices

- **Read the indicator bundle** — do not recompute. Guard warm-up with
  `if key is None: return`.
- **Be position-aware** — BUY when flat/short, SELL when flat/long; avoid stacking.
- **Never call `datetime.now()`** — use `self.ctx.now()`.
- **Filter by symbol** — return early if `event.symbol != self.symbol`.
- **Strategy errors are swallowed** — a broken hook never kills the kernel, so
  test hooks with hand-built events and write defensively.

---

Next: [Scanners](04-scanners.md) · Back: [Core Journey](02-core-journey.md) · [Index](index.md)
