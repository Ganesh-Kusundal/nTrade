# Task 1 Report: PaperBroker authoritative balance + positions

## Status: DONE

## Commit
`bdee309` — `fix: paper broker reports authoritative balance/positions (no sync wipe)`
(2 files: `ntrade/brokers/paper.py` +42/-1, `tests/test_paper_broker_state.py` +24)

## What was done
- `ntrade/brokers/paper.py`:
  - Imported `Position` from `ntrade.domain.portfolio`.
  - Added `self._positions: dict[str, Position] = {}` in `__init__`.
  - Added authoritative state tracking in `place_order` (after `self._orders.append(order)`): fills mutate `_balance` (BUY `-= notional`, SELL `+= notional`) and `_positions` (open, average-up, partial-close, close, exit-and-reverse), per the brief's exact block.
  - `get_positions()` now returns `list(self._positions.values())`; `get_balance()` returns tracked `self._balance`.
- Created `tests/test_paper_broker_state.py` (verbatim from the brief).

## Deviation from brief code (documented)
The brief's block used `product=order.product or "MIS"`. `Order` (ntrade/domain/orders/order.py) has **no** `product` attribute — its product field is `trade_type: TradeType` (values MIS/CNC/MARGIN/MTF). As written the brief's code would raise `AttributeError`. Fixed pragmatically to `product=order.trade_type.value` (guaranteed non-None, always a valid product string; e.g. equity defaults to CNC, futures to MIS). All other code used verbatim — `order.side.value == "BUY"` is correct (`OrderSide(str, Enum)` with values "BUY"/"SELL", matching existing `.value` style at paper.py:194).

## Test results
- Red: `python -m pytest tests/test_paper_broker_state.py -q` → `1 failed in 11.68s` (balance must decrease on a buy fill — `get_positions()`/`get_balance()` static).
- Green + existing paper suites: `python -m pytest tests/test_paper_broker_state.py tests/test_paper_broker_reject.py tests/test_paper_gate.py -q` → `11 passed in 15.37s`.
- Zero-parity: `python -m pytest tests/test_zero_parity_across_modes.py -q` → `3 passed in 161.21s (0:02:41)` (paper fills unchanged — parity preserved). NOTE: this suite exceeds the default 120s bash timeout because it runs real-time `LiveRunner.run(duration=10.0)` loops (12 mode-runs ≈ 2:40); run with a longer timeout.

## Concerns
- Paper balance excludes PnL on price movement between entry/exit in `get_balance()` semantics: balance reflects notional cash flow only (per brief's cost-free constraint); unrealized PnL lives in `Position.pnl`, not balance. This is by design for this task.
- `fill_price` for the tracking block uses `order.avg_price` (set earlier in `place_order`), so the block always sees the actual fill price — good.
- Unrelated uncommitted changes (strategies.py, indicators.py, data parquets, ui/, etc.) were left unstaged, as required.
