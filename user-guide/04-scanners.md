# Scanners

A scanner screens many instruments for a market condition and returns ranked
hits — each with a signal (`BUY` / `SELL` / `NEUTRAL`), a score, and the
conditions that matched. Use scanners to build a watchlist; use strategies to
trade.

Access scanners through the session:

```python
from ntrade import TradingSession

session = TradingSession.paper(initial_cash=100_000.0)
session.register(session.stock("TCS"))
session.register(session.stock("INFY"))
session.register(session.index("NIFTY"))

# Refresh quotes so scanners have data to work with
for inst in (session.stock("TCS"), session.stock("INFY"), session.index("NIFTY")):
    inst.market.refresh()

results = session.scanner().breakout(min_volume=100_000)
for r in results:
    print(f"{r.instrument.symbol}: {r.signal} score={r.score:.2f} rank={r.rank}")
```

Each `ScannerResult` carries the live `Instrument`, so you can immediately
read quotes, compute analytics, or place an order on a hit.

---

## Built-in scanners

| Method | Scanner | What it finds |
|--------|---------|---------------|
| `.gap(min_gap_pct=1.0)` | Gap | Gap up/down vs previous close beyond a % threshold |
| `.volume(min_volume=100_000, spike_multiplier=2.0)` | Volume spike | Volume well above average (or above an absolute floor) |
| `.momentum(rsi_threshold=60.0, min_change_pct=1.0)` | Momentum | Strong RSI or large % move from previous close |
| `.breakout(...)` | Breakout | Price breaking recent highs/lows |
| `.imbalance(...)` | Imbalance | Order-book imbalance between bid and ask |

```python
scanners = session.scanner()

scanners.gap(min_gap_pct=1.5)
scanners.volume(min_volume=100_000, spike_multiplier=2.0)
scanners.momentum(rsi_threshold=60.0)
scanners.breakout()
scanners.imbalance()
```

Instruments with no quote or indicators are skipped silently. Some scanners
throttle re-scans (for example volume and momentum wait ~30 seconds) so a hot
loop does not re-scan the whole universe every tick.

---

## Reading a result

```python
r = results[0]
r.instrument          # Instrument — call .market.ltp(), .order.buy(), ...
r.scanner_name        # e.g. "breakout"
r.score               # higher = stronger match
r.signal              # "BUY" | "SELL" | "NEUTRAL"
r.matched_conditions  # e.g. ("gap_up",)
r.indicator_values    # e.g. {"gap_pct": 2.1}
r.rank                # 1 = top hit
```

---

## Custom scanner

Subclass `Scanner`, set a `name`, implement `scan()`, and run it through the
facade:

```python
from ntrade import Scanner, ScannerResult, TradingSession


class HighPriceScanner(Scanner):
    name = "high_price"

    def scan(self, session, *, min_ltp: float = 1000.0, **kw):
        results = []
        for inst in session.kernel.ctx.instruments_snapshot():
            ltp = inst.market.ltp() or 0
            if ltp < min_ltp:
                continue
            results.append(ScannerResult(
                instrument=inst,
                scanner_name=self.name,
                score=ltp,
                signal="NEUTRAL",
                matched_conditions=("high_price",),
                indicator_values={"ltp": ltp},
            ))
        return results


session = TradingSession.paper()
session.register(session.stock("TCS"))
session.stock("TCS").market.refresh()

hits = session.scanner().custom(HighPriceScanner(), min_ltp=500.0)
# or register once and call by name later:
session.scanner().register(HighPriceScanner())
```

---

## Scanner vs strategy

| | Scanner | Strategy |
|-|---------|----------|
| Job | Screen a universe, return ranked hits | React to events, emit trade signals |
| Output | `list[ScannerResult]` | `SignalGeneratedEvent` → orders |
| Typical use | Morning watchlist, alerts | Automated entries and exits |

Scanners do not place orders. Hand a hit to a strategy, or place a manual
order on `r.instrument`.

---

Next: [Risk & Circuit Breakers](05-risk.md) · Back: [Strategies](03-strategies.md) · [Index](index.md)
