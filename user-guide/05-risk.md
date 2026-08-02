# Risk & Circuit Breakers

Every strategy signal is screened before it can become an order. Limits stop
oversized trades; circuit breakers halt trading when the session is in trouble.
While halted, every signal is rejected until you call `resume()`.

---

## 1. Per-strategy limits

Attach limits when you register a strategy. Each strategy gets its **own**
risk engine, so one strategy's caps do not affect another.

```python
from ntrade import TradingSession
from ntrade.engines.strategies import EmaCrossStrategy

session = TradingSession.paper(initial_cash=100_000.0)
session.register(session.index("NIFTY"))
session.register_strategy(
    EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol="NIFTY"),
    risk={
        "max_quantity": 25,              # reject if qty > 25
        "max_notional": 1_000_000,       # reject if price × qty > cap
        "max_positions": 2,              # reject if open positions ≥ 2
        "allowlist": {"NIFTY"},          # reject symbols not in the set
        "price_deviation_pct": 2.0,      # fat-finger guard vs last price
        "max_daily_loss": 5_000.0,       # halt if equity drops ₹5,000 from start
        "max_drawdown_pct": 5.0,         # halt if peak-to-trough drawdown > 5%
    },
)
```

| Kwarg                 | Rejects / halts when                                      |
|-----------------------|-----------------------------------------------------------|
| `max_quantity`        | signal quantity > cap                                     |
| `max_notional`        | price × quantity > cap                                    |
| `max_positions`       | open position count ≥ cap (this strategy only)            |
| `allowlist`           | symbol not in the allowed set                             |
| `price_deviation_pct` | price deviates > X% from last traded price (fat-finger)   |
| `max_daily_loss`      | session equity falls more than X from session start       |
| `max_drawdown_pct`    | equity drawdown from peak > X%                            |

`max_daily_loss` and `max_drawdown_pct` are **circuit breakers**: once tripped
they call `halt()`, and every later signal is rejected with
`risk halted: <reason>` until `resume()`.

---

## 2. Halt and resume

```python
# The RiskEngine lives on the kernel (global) or on the runner (per strategy).
risk = session.kernel.risk_engine

risk.equity()                 # current equity (cash + MTM)
risk.halt("manual stop")      # reject all signals; publishes RiskHaltedEvent
risk.resume()                 # clear the halt; publishes RiskResumedEvent
```

Watch screening outcomes in the event history:

```python
from ntrade import SignalApprovedEvent, SignalRejectedEvent, RiskHaltedEvent

approved = [e for e in session.kernel.bus.history if isinstance(e, SignalApprovedEvent)]
rejected = [e for e in session.kernel.bus.history if isinstance(e, SignalRejectedEvent)]
halts    = [e for e in session.kernel.bus.history if isinstance(e, RiskHaltedEvent)]

for e in rejected:
    print(e.reason)           # why it was blocked
```

---

## 3. Live kill switch

In live mode, `LiveRunner` listens for `RiskHaltedEvent`. When a breaker
trips, it activates the broker kill switch on every broker-backed instrument:

```
RiskHaltedEvent  →  instrument.broker.kill_switch(action="ACTIVATE")
```

That stops new orders at the broker. The wiring is automatic when you run
through `LiveRunner` (see [Simulation — paper to live](06-simulation.md)).
Paper mode has no broker kill switch — the halt still rejects signals locally.

---

## 4. Practical defaults

Start conservative on paper, then tighten for live:

```python
risk={
    "max_quantity": 10,
    "max_notional": 250_000,
    "max_positions": 1,
    "allowlist": {"NIFTY"},
    "price_deviation_pct": 1.5,
    "max_daily_loss": 2_000.0,
    "max_drawdown_pct": 3.0,
}
```

Check `session.runner.status()` for approve/reject counts while a strategy is
running. If rejects spike, print `e.reason` on each `SignalRejectedEvent`
before loosening a limit.

---

Next: [Backtest, Replay & Simulation](06-simulation.md) · Back: [Scanners](04-scanners.md) · [Index](index.md)
