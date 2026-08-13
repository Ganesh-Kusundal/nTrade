# Task 1 Brief: PaperBroker authoritative balance + positions

## Where this fits

Project: nTrade. The multi-agent review found a critical bug: `PositionSyncEngine` treats the broker as truth, but `PaperBroker.get_positions()` returns `[]` and `get_balance()` returns a static balance — so every sync interval drops all kernel positions and resets cash. This task makes PaperBroker report authoritative state (fills mutate balance + positions).

## Requirements (from the plan)

**Files:**
- Modify: `ntrade/brokers/paper.py` (init, `place_order`, `get_positions`, `get_balance`)
- Test: `tests/test_paper_broker_state.py` (new file — no general paper test file exists; the plan's "find existing" step resolves to creating one)

**Interfaces:**
- Produces: `PaperBroker._balance` tracked on fills; `get_positions()` returns tracked `Position` list; `get_balance()` returns tracked balance.

**Step 1: Write the failing test**

Create `tests/test_paper_broker_state.py`:

```python
"""PaperBroker must report authoritative balance + positions from its fills
(so PositionSyncEngine reconciles paper to its own reality instead of wiping
the kernel). Regression for the paper/synth position-sync wipe bug."""

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.orders.order import OrderType


def test_paper_broker_reports_authoritative_balance_and_positions():
    broker = PaperBroker()
    rel = Equity("RELIANCE", broker=broker)
    broker.seed_quote("RELIANCE", 100.0)

    # Buy 10 @ 100 -> balance decreases; a position appears.
    rel.order.place("BUY", 10, order_type=OrderType.MARKET)
    assert broker.get_balance() < 100_000.0, "balance must decrease on a buy fill"
    pos = broker.get_positions()
    assert len(pos) == 1 and pos[0].quantity == 10, "position must be tracked"

    # Sell 10 @ 101 -> back to flat; balance increases by the sell notional.
    rel.order.place("SELL", 10, order_type=OrderType.MARKET, price=101.0)
    assert broker.get_positions() == [], "position must close on the sell"
    assert broker.get_balance() > 99_900.0, "balance must recover on the sell"
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_paper_broker_state.py -q`
Expected: FAIL — `get_positions()` returns `[]`.

**Step 3: Track balance and positions in `place_order`**

Add `from ntrade.domain.portfolio import Position` to the imports in `ntrade/brokers/paper.py`.

In `__init__` (after `self._balance = 100_000.0` at paper.py:34), add:

```python
        self._positions: dict[str, Position] = {}
```

In `place_order`, after `self._orders.append(order)` (currently paper.py:145), add:

```python
        # Authoritative broker state: fills mutate balance + positions so the
        # PositionSyncEngine reconciles paper to its own reality instead of
        # wiping the kernel (the old get_positions()=[] wiped everything).
        fill_price = order.avg_price
        notional = fill_price * order.quantity
        pos = self._positions.get(order.instrument.symbol)
        if order.side.value == "BUY":
            self._balance = round(self._balance - notional, 4)
            qty = (pos.quantity if pos else 0) + order.quantity
            if pos is None:
                pos = Position(symbol=order.instrument.symbol, quantity=qty,
                               avg_price=fill_price, ltp=fill_price,
                               product=order.product or "MIS")
                self._positions[order.instrument.symbol] = pos
            else:
                if pos.quantity * qty >= 0:
                    total = pos.avg_price * abs(pos.quantity) + fill_price * order.quantity
                    pos.avg_price = round(total / abs(qty), 4)
                else:
                    pos.avg_price = fill_price
                pos.quantity = qty
                pos.ltp = fill_price
        else:  # SELL
            self._balance = round(self._balance + notional, 4)
            qty = (pos.quantity if pos else 0) - order.quantity
            if pos is None:
                pos = Position(symbol=order.instrument.symbol, quantity=qty,
                               avg_price=fill_price, ltp=fill_price,
                               product=order.product or "MIS")
                self._positions[order.instrument.symbol] = pos
            else:
                pos.quantity = qty
                pos.ltp = fill_price
                # exit-and-reverse: the residual opens a fresh short
                if pos.quantity * pos.avg_price < 0:
                    pos.avg_price = fill_price
            if qty == 0:
                del self._positions[order.instrument.symbol]
```

Note: `order.side` is an `OrderSide(str, Enum)` — verify `.value` is `"BUY"`/`"SELL"` by checking `ntrade/domain/orders/order.py`; if the enum values are `BUY`/`SELL`, use `order.side == OrderSide.BUY` instead (import `OrderSide`). Match the existing codebase style.

**Step 4: Update `get_positions` and `get_balance`**

```python
    def get_balance(self) -> float:
        return self._balance

    def get_positions(self):
        return list(self._positions.values())
```

**Step 5: Run the test to verify it passes + existing paper tests**

Run: `python -m pytest tests/test_paper_broker_state.py tests/test_paper_broker_reject.py tests/test_paper_gate.py -q`
Expected: all pass — the new state test and all existing paper tests (verify none asserted `get_positions()==[]` for a filled book).

**Step 6: Commit**

```bash
git add ntrade/brokers/paper.py tests/test_paper_broker_state.py
git commit -m "fix: paper broker reports authoritative balance/positions (no sync wipe)"
```

## Global Constraints (apply to this task)

- Modify ONLY `ntrade/brokers/paper.py` and the new `tests/test_paper_broker_state.py`.
- The balance formula: BUY `balance -= notional`, SELL `balance += notional` (paper pipeline is cost-free; real costs are applied by BrokerExecution on the kernel side).
- Keep it lazy: no new modules, no refactors beyond the targeted state tracking.

## Report contract

Write your report to `.superpowers/sdd/briefs/task-1-report.md`. Report:
status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED), the commit hash,
a one-line test summary with the pytest output line, and any concerns.
