# Task Group 1 report — B-006 / F-004: live broker fills pay commission + statutory

**Status:** DONE

**Scope:** `BrokerExecution._emit_fill` now computes commission + statutory exactly like
`SimulatedExecution`; the live kernel target threads the session's `statutory` param, so
paper/backtest PnL converges on live.

## What changed

### `ntrade/execution/broker_executor.py`
- Imported `CommissionModel`, `FlatCommission`, `IndianStatutoryCosts`, `STATUTORY_DEFAULT`,
  `resolve_statutory` from `ntrade.execution.costs`.
- `__init__(self, context, broker, *, commission: CommissionModel | None = None,
  statutory=STATUTORY_DEFAULT)` — mirrors `SimulatedExecution.__init__`:
  `self.commission = commission or FlatCommission(0.0)` and
  `self.statutory: IndianStatutoryCosts | None = resolve_statutory(statutory)`
  (`None` → zero-cost opt-out, `STATUTORY_DEFAULT` → `IndianStatutoryCosts()`). New params are
  keyword-only with defaults, so all three existing construction sites keep working.
- `_emit_fill`: after computing `price`, resolves the instrument via
  `self.ctx.instrument(intent.symbol)` (guarded for `None` → falls back to the configured
  model's default product schedule, never crashes) and computes
  `notional = price * new_qty`, `commission = round(self.commission.apply(notional), 4)`,
  `statutory = 0.0 if self.statutory is None else round(model.total_cost(notional, intent.side, brokerage=commission), 4)`.
  Both values are passed into the `OrderFilledEvent` (previously left at the `0.0` defaults).

### `ntrade/kernel/session.py`
- `BrokerExecution(self.ctx, broker)` → `BrokerExecution(self.ctx, broker, statutory=statutory)`
  (session already defaults `statutory=STATUTORY_DEFAULT`, so live now charges by default exactly
  like the sim target).

### `tests/test_live_execution.py`
- New failing-first test `test_live_broker_fill_charges_statutory_by_default`: live kernel with a
  fill-on-poll broker emits `OrderFilledEvent` with `commission == 0.0` (FlatCommission default)
  and `statutory > 0.0` under default wiring; also asserts the live statutory charge matches the
  simulated target for the same notional.
- New failing-first test `test_broker_execution_flat_commission_statutory_none_opt_out`:
  `BrokerExecution(ctx, broker, commission=FlatCommission(5.0), statutory=None)` emits
  `commission == 5.0` and `statutory == 0.0` (zero-cost opt-out preserved).
- Updated `test_live_kernel_zero_parity_with_simulated`: passes `statutory=None` to the live
  kernel too, so both sides share the zero-cost setting and the parity assertions (balance,
  fill price/qty, position) remain meaningful.

## Verification

Both new tests were written first and observed failing (statutory `0.0`; `TypeError: unexpected
keyword argument 'commission'`), then the implementation made them pass.

- Targeted: `./.venv/bin/python -m pytest -q tests/test_live_execution.py tests/test_broker_executor.py tests/test_findings_batch3.py`
  → `33 passed in 0.33s`
- Full suite: `./.venv/bin/python -m pytest -q` → `618 passed in 5.66s`
  (baseline 616 + the 2 new tests; `test_broker_executor.py` stale-eviction and
  `test_findings_batch3.py` M2 tests still pass — they construct `BrokerExecution(ctx, broker)`
  with default args and assert only on `open_orders()`).

## Commits

- `38d5e61` — "H6 live broker fills pay commission + statutory; live/sim parity"

Only the three task files were staged/committed (`ntrade/execution/broker_executor.py`,
`ntrade/kernel/session.py`, `tests/test_live_execution.py`). Pre-existing uncommitted changes to
`ntrade/brokers/dhan*.py` and the kanban/sdd docs were left untouched and unstaged.

## Concerns

- None blocking. Live statutory on the fill event now flows into `PortfolioEngine.on_filled`
  (portfolio_engine.py:51 `charges = event.commission + event.statutory`), which is what makes
  live balance PnL converge on paper. The delivery-detection uplift that the simulator applies to
  overnight equity round trips (simulator.py:105-126) is intentionally not mirrored here — live
  fills arrive one leg at a time and the broker's own payout is authoritative; flagging for
  awareness only.
