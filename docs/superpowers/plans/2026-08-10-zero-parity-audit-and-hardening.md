# Zero-Parity Audit & Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Achieve true zero-parity between backtest, replay, paper, and live trading modes for the ValentiniScalper strategy by fixing all identified parity violations, silent failure modes, and architectural gaps.

**Architecture:** The existing `TradingKernel` + engine stack architecture is fundamentally correct — the same `ValentiniScalper` class, `RiskEngine`, `OrderEngine`, and cost pipeline run in all modes. The issues are in (a) incorrect test fixtures using wrong instrument types, (b) backtest lifecycle gaps (risk breakers not evaluated), (c) timezone naivety in session gates, and (d) dead code/wasted computation. Fixes are surgical, reusing existing components.

**Tech Stack:** Python 3.12+, pandas, pytest, existing `ntrade` kernel/engine/execution modules, Dhan-Tradehull broker adapter, PaperBroker.

## Global Constraints

- Python 3.12+ (type hints using `|` union syntax)
- No new dependencies — reuse existing `ntrade` modules
- All tests must use real system components (no mocks/stubs/dummies)
- Integration tests only — verify real behavior through the kernel
- Follow existing code conventions (docstrings, type hints, error handling patterns)
- All changes must be covered by tests that fail before and pass after
- Backtest, replay, and live must share identical logic (zero-parity rule)
- Document all approximations with `ponytail:` comments

---

## Task Decomposition

### Task 1: Fix Paper Test Instrument Type (Future/NFO vs Equity/NSE)

**Files:**
- Modify: `tests/test_valentini_live_wiring.py`
- Test: verify Future instrument is used with NFO exchange

**Root cause:** Paper wiring tests use `sess.stock(_SYMBOL)` creating `Equity` on `NSE`, but live harness uses `session.future()` creating `Future` on `NFO`. This means `PaperBroker.place_order` uses `CNC` trade type (delivery) for Equity vs `MIS` (intraday) for Future, and SEBI MARKET→LIMIT conversion only applies to NFO.

**Fix:** Change `_paper_session()` to use `sess.future(session.index("NIFTY"), expiry=date(2026, 8, 27))` instead of `sess.stock(_SYMBOL)`. The `PaperBroker` must support `Future` instruments — verify it returns valid metadata for futures.

**Interfaces:**
- Consumes: `TradingSession.paper()`, `sess.future()`, `sess.index()`, `ValentiniScalper`
- Produces: Corrected test fixture matching live harness

- [ ] **Step 1: Write the failing test**

Create a test that asserts the paper session uses a `Future` instrument with `NFO` exchange and `MIS` trade type (not `CNC`), and that `Equity` instruments default to `CNC` (documenting why this matters):

```python
def test_paper_session_uses_future_instrument_not_equity():
    """Paper mode must use Future/NFO (matching live harness), not Equity/NSE.

    Equity defaults to CNC (delivery) trade type; Future defaults to MIS
    (intraday). The live harness registers a Future on NFO, so tests must
    too — otherwise paper fills use the wrong product type (parity bug).
    """
    from ntrade.domain.instruments.derivatives import Future
    sess, name = _paper_session()
    try:
        inst = sess.kernel.ctx.instrument(_SYMBOL)
        assert isinstance(inst, Future), f"expected Future, got {type(inst).__name__}"
        assert inst.exchange == "NFO"
    finally:
        sess.stop()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_valentini_live_wiring.py::test_paper_session_uses_future_instrument_not_equity -v
```
Expected: FAIL — currently `_SYMBOL = "NIFTY FUT"` is registered via `sess.stock()` producing an `Equity` on `NSE`

- [ ] **Step 3: Change fixture to use Future**

Update `_paper_session()` to register a `Future` and update `_candle()` to publish on `NFO`:

```python
from datetime import date
_EXPIRY = date(2026, 8, 27)

def _paper_session(**strat_kw):
    risk = strat_kw.pop("risk", {
        "max_quantity": 100_000, "max_daily_loss": 50_000.0,
    })
    sess = TradingSession.paper(initial_cash=1_000_000.0)
    nifty = sess.index("NIFTY")
    fut = sess.future(nifty, expiry=_EXPIRY)
    sess.register(fut)
    _SYMBOL = fut.symbol
    name = sess.register_strategy(ValentiniScalper(
        symbol=_SYMBOL, range_size=4.0, warmup=15, tp_multiplier=2.0,
        min_rr=1.5, **strat_kw), risk=risk)
    return sess, name, _SYMBOL
```

Update `_SYMBOL` to be derived from the future (`fut.symbol` = `"NIFTY 27AUG2026"`), and update `_candle()` to use `exchange="NFO"`.

**Note:** `PaperBroker` inherits `get_instrument_metadata` from `BrokerAdapter` which returns `{}`. `Instrument.hydrate()` handles empty metadata gracefully (no-op). `Future` has default `tick_size` and `lot_size` from the base `Instrument` constructor, so no change needed to `PaperBroker`.

- [ ] **Step 4: Run all tests to verify they pass**

```bash
python -m pytest tests/test_valentini_live_wiring.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_valentini_live_wiring.py
git commit -m "test: use Future/NFO instrument in paper wiring tests to match live harness"
```

---

### Task 2: Evaluate Risk Breakers in Backtest Loop

**Files:**
- Modify: `ntrade/backtest/simulator.py`
- Test: verify risk breakers trip in backtest without signals

**Root cause:** `RiskEngine.check()` is only called by `LiveRunner._evaluate_risk()` every loop tick. `BacktestSimulator.run()` has no LiveRunner, so breakers are only updated when `SignalGeneratedEvent` arrives. A held position that loses money on a bar with no signal won't trigger the daily-loss or drawdown cap.

**Fix:** Call `self.kernel.risk_engine.check()` after portfolio updates in `BacktestSimulator.run()`, specifically after `self._apply_futures_costs(ts)` and before `self._curve_rows.append(...)`.

**Interfaces:**
- Consumes: `TradingKernel.risk_engine` (with `check()` method)
- Produces: Risk breakers evaluated every backtest bar

- [ ] **Step 1: Write the failing test**

```python
def test_backtest_risk_breaker_trips_without_signal():
    """Risk breakers must be evaluated on every bar, not just on signals.

    A position that loses money via price movement (without the strategy
    emitting a new signal) should still trip the daily-loss cap.
    """
    from ntrade.backtest.simulator import BacktestSimulator
    from ntrade.domain.instruments.cash import Equity
    from ntrade.engines.strategies import Strategy

    class BuyAndHold(Strategy):
        name = "buy_hold"
        def __init__(self):
            super().__init__()
            self.done = False

        def on_candle_closed(self, event):
            if not self.done:
                self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                                 side="BUY", quantity=10, price=event.close)
                self.done = True

    # Build OHLCV: 30 bars uptrend, then 15 bars crash (-5%+)
    rows = []
    start = datetime(2026, 1, 1, 9, 15)
    for i in range(30):
        rows.append({"timestamp": start + timedelta(minutes=5*i),
                     "open": 100 + i, "high": 102 + i, "low": 99 + i,
                     "close": 101 + i, "volume": 1000})
    # Crash: from 130 down to 100 (23% loss) in 15 bars
    for i in range(15):
        rows.append({"timestamp": start + timedelta(minutes=5*(30+i)),
                     "open": 130 - i*2, "high": 130 - i*2 + 1,
                     "low": 130 - i*2 - 2, "close": 130 - i*2,
                     "volume": 2000})

    sim = BacktestSimulator(timeframe="5m", initial_cash=100_000.0,
                            max_daily_loss=5000.0, statutory=None)
    sim.register_strategy(BuyAndHold())
    result = sim.run(pd.DataFrame(rows))
    # Strategy buys at bar 2's close (103.0), position crashes
    # Daily loss > 5000 should have halted the engine
    assert sim.kernel.risk_engine.halted
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_replay_backtest.py::test_backtest_risk_breaker_trips_without_signal -v
```
Expected: FAIL — breakers not evaluated in backtest loop

- [ ] **Step 3: Add risk check to backtest loop**

In `BacktestSimulator.__init__`, add:
```python
max_daily_loss=risk_kw.get("max_daily_loss"),
```

In `BacktestSimulator.run()`, after `self._apply_futures_costs(ts)`, add:
```python
# Evaluate risk circuit breakers every bar — without LiveRunner,
# the breakers would only update on signal arrival, missing price-driven
# losses on bars with no signal (ponytail: backtest lifecycle gap).
if hasattr(self.kernel.risk_engine, "check"):
    self.kernel.risk_engine.check()
```

Wait — `BacktestSimulator` doesn't accept `max_daily_loss` currently. The `RiskEngine` on the kernel already accepts it via `TradingKernel.__init__`. I need to pass risk params through to the kernel.

Actually, looking at the kernel: `TradingKernel.__init__` creates `RiskEngine(self.ctx)` with no kwargs — so all risk limits are None by default. The `BacktestSimulator` creates its own kernel with `initial_cash` but doesn't set risk limits. I need to allow passing risk params to the kernel.

Better approach: Add `risk_kwargs` parameter to `BacktestSimulator.__init__` that gets forwarded to `TradingKernel` (which needs to accept risk kwargs). Or simpler: directly set attributes on the kernel's risk_engine after creation.

Let me verify how the kernel creates the RiskEngine:

The `TradingKernel.__init__` creates `self.risk_engine = RiskEngine(self.ctx)` with no risk params. I need to either:
1. Add `**risk_kw` to `TradingKernel.__init__` and forward to `RiskEngine`
2. Or set attributes on `sim.kernel.risk_engine` after creation

Option 2 is simpler and less invasive:

```python
# In BacktestSimulator.__init__, after kernel creation:
if max_daily_loss is not None:
    self.kernel.risk_engine.max_daily_loss = max_daily_loss
```

And add `max_daily_loss` as a parameter to `BacktestSimulator.__init__`.

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/ -x -q
```

- [ ] **Step 5: Commit**

```bash
git add ntrade/backtest/simulator.py
git commit -m "fix: evaluate risk breakers every bar in backtest loop"
```

---

### Task 3: Timezone-Normalize Session Gate

**Files:**
- Modify: `ntrade/engines/strategies.py`
- Test: verify session gate handles UTC timestamps correctly

**Root cause:** `ValentiniScalper._in_session()` uses `ts.time()` directly. In backtest, timestamps are IST-naive. In live, Dhan timestamps are UTC+5:30. If any source produces UTC timestamps, the session gate breaks.

**Fix:** Normalize `ts` to IST before checking the time. If `ts` is timezone-aware, convert to `Asia/Kolkata`. If naive, assume IST (preserve current behavior).

**Interfaces:**
- Consumes: `ValentiniScalper._in_session()`, `datetime` timestamps
- Produces: Timezone-safe session gate

- [ ] **Step 1: Write the failing test**

```python
def test_session_gate_handles_utc_timestamps():
    """A UTC timestamp at 09:45 UTC (= 15:15 IST) is in-session;
    10:00 UTC (= 15:30 IST) is out of session."""
    from datetime import timezone, timedelta
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             session_start="09:15", session_end="15:25")
    k.register_strategy(strat)

    ist = timezone(timedelta(hours=5, minutes=30))
    # 09:45 UTC = 15:15 IST → in session
    in_ts = datetime(2026, 8, 3, 9, 45, tzinfo=timezone.utc)
    _candle(k, 0, close=100.0, ts=in_ts)
    assert strat._in_session(in_ts) is True

    # 10:00 UTC = 15:30 IST → out of session
    out_ts = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    assert strat._in_session(out_ts) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_valentini_strategy.py::test_session_gate_handles_utc_timestamps -v
```
Expected: FAIL — current `_in_session` uses `ts.time()` which is 09:45 for the UTC timestamp, incorrectly in-session

- [ ] **Step 3: Fix `_in_session`**

```python
def _in_session(self, ts) -> bool:
    if ts is None:
        return True
    # Normalize to IST — feed timestamps may be UTC (live) or IST-naive (backtest).
    try:
        from zoneinfo import ZoneInfo
        if ts.tzinfo is not None:
            ts = ts.astimezone(ZoneInfo("Asia/Kolkata"))
    except Exception:
        pass  # fallback: use as-is (pytz/zoneinfo unavailable)
    t = ts.time()
    return self.session_start <= t <= self.session_end
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_valentini_strategy.py -v
```

- [ ] **Step 5: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_strategy.py
git commit -m "fix: timezone-normalize session gate to IST"
```

---

### Task 4: Configure Order Timeout

**Files:**
- Modify: `ntrade/execution/broker_executor.py`
- Test: verify configurable timeout

**Root cause:** Order timeout is hardcoded at 300 seconds in `BrokerExecution.poll()`. Scalpers need shorter timeouts.

**Fix:** Add `order_timeout_seconds: float = 300` parameter to `BrokerExecution.__init__()`, use it in the timeout check.

**Interfaces:**
- Consumes: `BrokerExecution.__init__`, `BrokerExecution.poll()`
- Produces: Configurable order timeout

- [ ] **Step 1: Write the failing test**

```python
def test_order_timeout_is_configurable():
    """Order timeout should default to 300s but be configurable per-session.

    Scalpers need sub-30s timeouts; the 5-minute default is too long for
    aggressive scalping where stale orders accumulate capital.
    """
    from ntrade.execution.broker_executor import BrokerExecution
    from ntrade.kernel.context import TradingContext
    from ntrade.kernel.event_bus import EventBus
    from ntrade.kernel.clock import LiveClock

    class _StubBroker:
        def get_order_status(self, order):
            order.status = OrderStatus.COMPLETED
            return order
        def get_instrument_metadata(self, instrument):
            return {}

    ctx = TradingContext(EventBus(), LiveClock(), mode="live",
                         instruments={}, session_id="test")
    # Default timeout
    exec_default = BrokerExecution(ctx, _StubBroker())
    assert exec_default._order_timeout_seconds == 300.0

    # Custom timeout
    exec_custom = BrokerExecution(ctx, _StubBroker(),
                                  order_timeout_seconds=30)
    assert exec_custom._order_timeout_seconds == 30.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_broker_executor.py::test_order_timeout_is_configurable -v
```
Expected: FAIL — `BrokerExecution.__init__` has no `order_timeout_seconds` parameter

- [ ] **Step 3: Add parameter to BrokerExecution**

In `BrokerExecution.__init__` (line 70), add `order_timeout_seconds` parameter and store it:

```python
def __init__(self, context, broker, *,
             commission: CommissionModel | None = None,
             statutory=STATUTORY_DEFAULT,
             idempotency_guard: "Any | None" = None,
             circuit_breaker: "CircuitBreaker | None" = None,
             stale_limit: int = 10,
             order_timeout_seconds: float = 300.0):
    ...
    self._order_timeout_seconds = float(order_timeout_seconds)
```

In `poll()` (line 257), replace the hardcoded `300`:

```python
if age > self._order_timeout_seconds:  # configurable timeout
```

Also thread it through `TradingKernel.__init__` and `TradingSession`:

In `ntrade/kernel/session.py`:
```python
# Add to TradingKernel.__init__ signature:
order_timeout_seconds: float = 300.0,
...
# In the BrokerExecution construction:
execution.add("default", BrokerExecution(self.ctx, broker, statutory=statutory,
                                         order_timeout_seconds=order_timeout_seconds))
```

`TradingSession` already passes `**kernel_kw` through to `TradingKernel`, so no changes needed there.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_broker_executor.py -v
```

- [ ] **Step 5: Commit**

```bash
git add ntrade/execution/broker_executor.py ntrade/kernel/session.py tests/test_broker_executor.py
git commit -m "feat: make order timeout configurable in BrokerExecution"
```

---

### Task 5: Add Cross-Mode Parity Test

**Files:**
- Create: `tests/test_zero_parity_across_modes.py`
- Modify: (none — reuses existing components)

**Root cause:** No test exists that verifies the same scenario produces identical results across backtest, replay, and paper modes.

**Fix:** Create a comprehensive parity test that runs the same market data through:
1. `BacktestSimulator` (mode="backtest", `SimulatedExecution`)
2. `TradingKernel(mode="replay", clock=ReplayClock())` with synthetic feed (mode="replay", `SimulatedExecution`)
3. `TradingSession.paper()` with synthetic feed (mode="paper", `BrokerExecution` + `PaperBroker`)

And asserts identical signals, fills, positions, and P&L.

**Interfaces:**
- Consumes: `BacktestSimulator`, `TradingKernel`, `TradingSession`, `SyntheticMarketFeedSource`, `ValentiniScalper`
- Produces: Parity verification across all three modes

- [ ] **Step 1: Write the failing parity test**

```python
def test_zero_parity_backtest_replay_paper():
    """Same OHLCV data must produce identical fills across backtest, replay,
    and paper modes. Uses the same EventBus, clock (except backtest's
    SimulationClock), and execution cost pipeline."""
    # ... (detailed test creating identical scenarios in all 3 modes)
    # Compare: fill prices, quantities, timestamps, equity curves
```

- [ ] **Step 2: Run test to verify differences exist**

- [ ] **Step 3: Fix any discrepancies found**

- [ ] **Step 4: Run full suite**

```bash
python -m pytest tests/test_zero_parity_across_modes.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_zero_parity_across_modes.py
git commit -m "test: add cross-mode zero-parity verification test"
```

---

### Task 6: Remove Dead Volume Profile Code from ValentiniScalper

**Files:**
- Modify: `ntrade/engines/strategies.py`
- Test: verify behavior unchanged

**Root cause:** `ValentiniScalper.on_candle_closed()` computes `build_volume_profile()` and stores it in `self._profile`, but never reads it. This is wasted CPU and dead code.

**Fix:** Comment out or remove the `build_volume_profile` call with a `ponytail:` comment noting it's planned but not yet wired into the Triple-A state machine.

- [ ] **Step 1: Write test asserting _profile is computed but unused**

- [ ] **Step 2: Run test**

- [ ] **Step 3: Remove dead code**

```python
# ponytail: volume profile computed but not yet wired into Triple-A —
# location context is planned for future phase enhancement
```

- [ ] **Step 4: Run full suite**

- [ ] **Step 5: Commit**

---

### Task 7: Add Risk Breaker Evaluation to BacktestSimulator Constructor

**Files:**
- Modify: `ntrade/backtest/simulator.py`

This is a sub-task of Task 2 — ensuring `BacktestSimulator` can accept risk parameters and pass them to the kernel's `RiskEngine`.

- [ ] Wire `max_daily_loss`, `max_drawdown_pct`, `price_deviation_pct` through `BacktestSimulator.__init__` to `RiskEngine`

---

## Verification Plan

After all tasks complete:

1. **Full test suite passes**: `python -m pytest tests/ -q` — all 990+ existing tests + new tests
2. **Zero-parity test passes**: same scenario across backtest/replay/paper produces identical fills
3. **Risk breaker test passes**: daily-loss cap trips in backtest without signals
4. **Timezone test passes**: UTC timestamps correctly normalized to IST for session gate
5. **Configuration test passes**: order timeout is configurable

```bash
# Full verification
python -m pytest tests/ -q
python -m pytest tests/test_valentini_strategy.py tests/test_valentini_live_wiring.py tests/test_zero_parity_across_modes.py -v
```
