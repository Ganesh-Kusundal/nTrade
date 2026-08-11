# Zero-Parity Fill Price Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the fill price divergence between backtest/replay/paper modes by carrying the bar-close reference price through the signal→intent→execution chain, so MARKET orders fill at the bar close that generated the signal — not at a contaminated `instrument._quote.ltp`.

**Architecture:** The existing engine stack (Market → Candle → Indicator → Strategy → Risk → Order → Execution) is correct. The bug is that `SimulatedExecution` and `PaperBroker` both fill MARKET orders at `instrument._quote.ltp`, which has been contaminated by the next bar's market data by the time the strategy's signal fires (see Root Cause below). The fix adds a `reference_price` field to `OrderIntentEvent` and `Order`, and has all execution targets fill at that reference price when set, falling back to `_quote.ltp` only for live trading where the reference price is not yet known. This is a single, surgical change that converges all modes to the same fill price.

**Tech Stack:** Python 3.12+, pytest, existing `ntrade` kernel/engine/execution modules

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

## Root Cause: Fill Price Divergence

The `ValentiniScalper` fires on `CandleClosedEvent`, which carries `event.close` — the close of the bar that triggered the signal. The strategy emits a MARKET order (`price=0.0`) and the execution fills at `instrument._quote.ltp`.

The problem: `instrument._quote.ltp` is a mutable read-model updated by every market data event. By the time the `CandleClosedEvent` fires and the strategy executes, the LTP has been contaminated by the **next** bar's market data:

- **Backtest** (`BacktestSimulator.run`): For each bar, publishes `QuoteEvent(ltp=close_i)` then `TickEvent(price=close_i)`. The `CandleEngine.on_quote` (active in backtest mode) detects a new bucket when the **next** bar's `QuoteEvent` arrives and fires `CandleClosedEvent` for bar `i`. But the `QuoteEvent` for bar `i+1` has already updated `_quote.ltp = close_{i+1}`. → Fill at next bar's close (e.g., 116.5 instead of 116.04). **Look-ahead bias.**

- **Replay** (`SyntheticMarketFeedSource`): Publishes `QuoteEvent(ltp=close_i)` then 60 `TickEvent`s. The `CandleEngine.on_quote` is a no-op in replay. The `CandleEngine.on_tick` builds candles from ticks and fires `CandleClosedEvent` when the **first tick of bar i+1** arrives. That tick has already updated `_quote.ltp` via `ingest_tick`. → Fill at next bar's first tick price (≈ open).

- **Paper** (`BrokerExecution` + `PaperBroker`): Same as replay — `PaperBroker.place_order` reads `order.instrument._quote.ltp` at the time the order is placed, which is the next bar's tick price.

**Result**: backtest fills at `close_{i+1}`, replay/paper fill at `open_tick_{i+1}`. These diverge by the next bar's body size.

### The Fix

Carry the bar-close reference price through the signal→intent→execution chain:

1. **Add `reference_price` field to `OrderIntentEvent`** — when a MARKET signal fires from a `CandleClosedEvent`, the strategy populates `reference_price = event.close`.

2. **`OrderEngine.on_signal_approved`** transfers `reference_price` from signal metadata to the `OrderIntentEvent`.

3. **`SimulatedExecution.submit`** fills MARKET orders at `intent.reference_price` when set (instead of `instrument._quote.ltp`), falling back to `_quote.ltp` when `reference_price` is 0.0 (live trading where no candle bar is in scope).

4. **Add `reference_price` field to `Order`** — so `PaperBroker.place_order` can read it from the `Order` object.

5. **`BrokerExecution.submit`** passes `reference_price=intent.reference_price` through `instrument.order.place()` kwargs to the `Order`.

6. **`PaperBroker.place_order`** fills MARKET orders at `order.reference_price` when set, falling back to `live_ltp` for live.

7. **`BarAwareExecution`** inherits the fix from `SimulatedExecution` (its `super().submit()` call uses the parent's fill logic).

This is ONE conceptual fix applied at ONE boundary (the `OrderIntentEvent`/`Order`), with all three execution targets reading the same field. No mode-specific branching. Live mode (`DhanBroker`) is unaffected since `reference_price` is 0.0 (no bar context) and live uses its own `instrument.refresh()` + LIMIT conversion.

---

## File Structure

```
ntrade/events/order.py           — Add `reference_price` field to OrderIntentEvent
ntrade/engines/order_engine.py   — Pass reference_price from signal metadata to intent
ntrade/engines/strategy_engine.py — emit_signal accepts reference_price kwarg
ntrade/execution/simulator.py    — Fill at reference_price when set
ntrade/domain/orders/order.py    — Add `reference_price` field to Order dataclass
ntrade/brokers/paper.py          — PaperBroker fills at order.reference_price when set
ntrade/execution/broker_executor.py — Pass reference_price through to broker order
ntrade/engines/strategies.py     — ValentiniScalper passes bar close as reference_price
tests/test_order_reference_price.py — New: verify reference_price flows through chain
tests/test_zero_parity_across_modes.py — Updated parity test
```

---

## Task Decomposition

### Task A: Add `reference_price` to `OrderIntentEvent` and `Order`

**Files:**
- Modify: `ntrade/events/order.py` (add field to `OrderIntentEvent`)
- Modify: `ntrade/domain/orders/order.py` (add field to `Order` dataclass)
- Test: `tests/test_order_reference_price.py` (new, verifies both classes have the field)

**Interfaces:**
- Consumes: `Event` base class
- Produces: `OrderIntentEvent.reference_price: float = 0.0` and `Order.reference_price: float = 0.0`

The `reference_price` is 0.0 when not set (live trading or non-candle signals). When set (from a `CandleClosedEvent.close`), execution targets use it as the fill price for MARKET orders.

```python
# ntrade/events/order.py — OrderIntentEvent
@dataclass(frozen=True, kw_only=True)
class OrderIntentEvent(Event):
    symbol: str
    exchange: str
    side: str
    quantity: int
    order_type: str = "LIMIT"
    price: float = 0.0
    reference_price: float = 0.0  # bar close from CandleClosedEvent; 0.0 = use live LTP
    strategy: str = ""

# ntrade/domain/orders/order.py — Order
@dataclass
class Order:
    instrument: "Instrument"
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    trade_type: TradeType = TradeType.MIS
    price: float = 0.0
    reference_price: float = 0.0  # bar close carried from intent; 0.0 = live LTP
    trigger_price: float = 0.0
    ...
```

- [ ] **Step 1: Write the failing test**

```python
def test_reference_price_fields_exist():
    """Both OrderIntentEvent and Order must have reference_price field."""
    intent = OrderIntentEvent(
        symbol="TEST", exchange="NSE", side="BUY",
        quantity=100, order_type="MARKET", price=0.0,
    )
    assert intent.reference_price == 0.0

    intent_ref = OrderIntentEvent(
        symbol="TEST", exchange="NSE", side="BUY",
        quantity=100, order_type="MARKET", price=0.0,
        reference_price=116.04,
    )
    assert intent_ref.reference_price == 116.04
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add fields**

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add ntrade/events/order.py ntrade/domain/orders/order.py tests/test_order_reference_price.py
git commit -m "feat: add reference_price field to OrderIntentEvent and Order"
```

---

### Task B: Thread `reference_price` through `emit_signal` and `OrderEngine`

**Files:**
- Modify: `ntrade/engines/strategy_engine.py` — `emit_signal` accepts `reference_price` kwarg, passes into `**metadata`
- Modify: `ntrade/engines/order_engine.py` — `OrderEngine.on_signal_approved` reads `reference_price` from `signal.metadata`, sets on intent
- Modify: `ntrade/engines/strategies.py` — `ValentiniScalper._emit_entry` and `_exit` pass `reference_price=float(event.close)`
- Test: `tests/test_order_reference_price.py` — verify signal→intent carries reference_price

**Interfaces:**
- Consumes: `SignalGeneratedEvent.metadata`, `OrderIntentEvent.reference_price`
- Produces: `OrderIntentEvent` with `reference_price` populated from bar close

```python
# strategy_engine.py
def emit_signal(self, *, symbol, exchange="CASH", side, quantity,
                price=0.0, reference_price=0.0, **metadata):
    if reference_price:
        metadata["reference_price"] = reference_price
    signal = SignalGeneratedEvent(..., metadata=metadata, ...)

# order_engine.py
def on_signal_approved(self, event):
    signal = event.signal
    intent = OrderIntentEvent(
        ...
        order_type="LIMIT" if signal.price else "MARKET",
        price=signal.price,
        reference_price=signal.metadata.get("reference_price", 0.0),
        ...
    )

# strategies.py — _emit_entry
self.emit_signal(
    ..., price=0.0,
    reference_price=float(event.close),  # bar close from CandleClosedEvent
    ...
)

# strategies.py — _exit
self.emit_signal(
    ..., price=0.0,
    reference_price=float(event.close),
    ...
)
```

- [ ] **Step 1: Write the failing test**

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement changes in strategy_engine.py, order_engine.py, strategies.py**

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add ntrade/engines/strategy_engine.py ntrade/engines/order_engine.py ntrade/engines/strategies.py
git commit -m "feat: thread reference_price from bar-close through signal chain to intent"
```

---

### Task C: Update `SimulatedExecution` to fill at `reference_price` when set

**Files:**
- Modify: `ntrade/execution/simulator.py` — `submit()` uses `intent.reference_price` for MARKET fill base when > 0
- Test: `tests/test_order_reference_price.py` — verify fill at reference_price, not contaminated ltp

**Interfaces:**
- Consumes: `OrderIntentEvent.reference_price`, `instrument._quote.ltp`
- Produces: `OrderFilledEvent.fill_price` = reference_price when set, else ltp

```python
# simulator.py — in submit(), MARKET branch:
if intent.order_type == "MARKET":
    base = intent.reference_price or (instrument._quote.ltp or 0.0)
    if base <= 0:
        return OrderRejectedEvent(...)
    fill_price = self.slippage.apply(base, intent.side)
```

- [ ] **Step 1: Write the failing test**

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement change**

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add ntrade/execution/simulator.py
git commit -m "fix: SimulatedExecution fills MARKET orders at reference_price when set"
```

---

### Task D: Update `PaperBroker` to fill at `reference_price` when set

**Files:**
- Modify: `ntrade/execution/broker_executor.py` — `BrokerExecution.submit` passes `reference_price=intent.reference_price` to `instrument.order.place()`
- Modify: `ntrade/brokers/paper.py` — `place_order` uses `order.reference_price` when > 0 for MARKET orders
- Test: `tests/test_zero_parity_across_modes.py` — full parity test passes

**Interfaces:**
- Consumes: `OrderIntentEvent.reference_price`
- Produces: `OrderFilledEvent.fill_price` = reference_price when set, else live LTP

`OrderFacade.place` already passes `**kwargs` to `Order()`, so `reference_price` flows through automatically. `PaperBroker.place_order` checks `order.reference_price` before falling back to `live_ltp`.

For **live** mode (`DhanBroker`): `reference_price` is 0.0 (no bar context), so paper/simulated modes use the bar-close reference while live uses its own `instrument.refresh()` + LIMIT conversion — zero impact on live.

```python
# broker_executor.py — in submit():
order = instrument.order.place(
    intent.side, intent.quantity,
    order_type=OrderType(intent.order_type.upper()),
    price=intent.price,
    reference_price=intent.reference_price,
)

# paper.py — place_order():
if order.order_type.value != "MARKET" and order.price:
    fill_price = order.price
elif order.reference_price > 0.0:
    fill_price = order.reference_price  # bar-close reference (zero-parity)
elif live_ltp > 0.0:
    fill_price = live_ltp
else:
    fill_price = self.get_quote(order.instrument).ltp
```

- [ ] **Step 1: Write the failing test** (update zero-parity test)

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement changes in broker_executor.py and paper.py**

- [ ] **Step 4: Run zero-parity test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add ntrade/execution/broker_executor.py ntrade/brokers/paper.py tests/test_zero_parity_across_modes.py
git commit -m "fix: PaperBroker fills MARKET orders at reference_price for zero-parity"
```

---

### Task F: Remove Dead Volume Profile Code from ValentiniScalper

**Files:**
- Modify: `ntrade/engines/strategies.py` — remove `build_volume_profile` call (Task 6 from original plan)
- Test: verify behavior unchanged

- [ ] **Step 1: Write test asserting `_profile` field exists but code path removed**

- [ ] **Step 2: Run test**

- [ ] **Step 3: Comment out the `build_volume_profile` call with `ponytail:` comment**

```python
# ponytail: volume profile computed but not yet wired into Triple-A state machine —
# removing dead computation to save CPU. Location context planned for future phase.
# self._profile = build_volume_profile(bars_tail, step=self._range_size or None)
```

- [ ] **Step 4: Run full suite**

- [ ] **Step 5: Commit**

```bash
git add ntrade/engines/strategies.py
git commit -m "chore: remove dead volume profile computation from ValentiniScalper"
```

---

## Verification Plan

After all tasks complete:

1. **Full test suite passes**: `python -m pytest tests/ -q` — all existing tests + new tests
2. **Zero-parity test passes**: same scenario across backtest/replay/paper produces identical fills
3. **Reference price test passes**: reference_price flows through signal→intent→execution chain
4. **Risk breaker test passes**: daily-loss cap trips in backtest without signals
5. **Determinism test passes**: repeat runs produce identical results
