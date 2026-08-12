# Priority-1 Engine & Parity Fixes — Design

## Problem

The multi-agent review (docs/superpowers/reviews/2026-08-12-multi-agent-review.md)
found 5 Critical/Important bugs that break zero-parity, corrupt metrics, or
crash shipped strategies:

1. **Paper/synth position-sync wipe** — `PositionSyncEngine` treats the broker
   as truth, but `PaperBroker.get_positions()` returns `[]` and
   `get_balance()` returns a static balance (never updated by fills). Every
   sync interval drops all kernel positions and resets cash to ₹1M.
2. **Backtest results truncated** — `results()` rebuilds trades/costs from
   `bus.history` (a `deque(maxlen=10_000)`); long runs silently lose early
   fills → wrong `n_trades`/win-rate/costs.
3. **Divergence-exit benchmark broken** — compares one range-bar's volume to
   the whole entry-leg's SUM × 0.6 → every higher-high range bar looks "weak"
   → the auction-following runner exits after 1-2 bars.
4. **Session-rollover `_rows` contamination** — `_rows` (→ `frame`) is never
   cleared at session rollover; the morning profile/VWAP/SL are built from
   prior-day bars until the 600-row window rolls them out.
5. **WIP strategies crash** — `GainzCloneStrategy` (`strategies.py:827` `.time`
   missing `()`; `:862/:867` `hma`/`rsi` not imported) and `VwapReclaimStrategy`
   (`:1016` `rsi` not imported) — both raise at runtime, zero tests.

## Changes

All fixes are localized. No new modules.

### Fix 1 — PaperBroker reports authoritative state

- `ntrade/brokers/paper.py`: make fills mutate broker state so the broker IS
  the truth it reports:
  - track `self._balance` — on each fill, `balance -= notional + commission`.
  - maintain `self._positions: dict[str, Position]` upserted on each fill
    (qty signed by side, avg_price = average, ltp = fill price).
  - `get_positions()` returns the tracked positions; `get_balance()` returns
    the tracked balance.
- `PositionSyncEngine.sync()` then reconciles kernel → same state (no-op), and
  paper mode keeps the identical sync pipeline as live.
- Rejected alternative: skipping `PositionSyncEngine` for PaperBroker — breaks
  the "identical pipeline in all modes" invariant.

### Fix 2 — BacktestSimulator keeps a dedicated fill list

- `ntrade/backtest/simulator.py`: maintain `self._fills: list[OrderFilledEvent]`
  appended in the fill handler (where `OrderFilledEvent` is emitted).
- `results()` rebuilds `trades`, `n_trades`, `commissions_total`,
  `statutory_total` from `self._fills` instead of `bus.history`.
- Exact metrics regardless of event volume (>10k events).

### Fix 3 — Divergence-exit per-bar benchmark

- `ntrade/engines/strategies.py:321-331`: change `act["impulse_volume"]` from
  the leg SUM to the leg per-bar MEAN (`leg["volume"].mean()`), so
  `_divergence_exit` compares a bar's volume against 0.6× the leg's average
  bar volume — genuine weakness, not leg-sum inflation.
- Port the same to `ui/src/lib/valentini.ts` (the TS mirror computes
  `impulseVolume` as the leg sum at entry) — parity.

### Fix 4 — Clear `_rows` at session rollover

- `ntrade/engines/strategies.py:442-454`: in the `key != self._session_key`
  block, after stashing `_prior_poc` and resetting `_profile`/`_leg_start_idx`,
  clear `_rows` down to today's bars only. The current bar was appended at the
  top of `on_candle_closed` (line 416) BEFORE the rollover check, so clearing
  must preserve that last row: `del self._rows[:-1]` (keep rows[0] if it's the
  current bar, else `self._rows[:] = [self._rows[-1]]`).
- `_prior_poc` (stashed before clearing) remains the explicit prior-day
  reference; morning profile/VWAP/SL are built from today's bars only.

### Fix 5 — WIP strategies: import + paren + tests

- `ntrade/engines/strategies.py:16`: add `hma, rsi` to the `indicators` import.
- `ntrade/engines/strategies.py:827`: `datetime.strptime(session_end, "%H:%M").time`
  → `.time()`.
- New smoke tests: instantiate `GainzCloneStrategy` + `VwapReclaimStrategy`,
  run a short backtest frame through each, assert no crash and a sane result.

## Data flow

- Paper: fill → PaperBroker state mutation → sync reconciles (no-op) → kernel
  positions/cash preserved.
- Backtest: fill → `self._fills.append` → `results()` reads exact list.
- Strategy: rollover clears `_rows` → today-only analytics; divergence
  compares per-bar means.

## Testing

- Fix 1: paper run where sync fires after fills → positions/cash survive;
  balance decreased by notional+commission.
- Fix 2: synthetic >10k-event run → `results().n_trades` matches actual fills.
- Fix 3: runner stays open past a normal-volume higher-high (no phantom
  divergence); TS mirror test locked to the same.
- Fix 4: multi-day frame → day-2 morning profile built from day-2 bars only
  (day-1 bars absent from the window).
- Fix 5: both strategies run a backtest without raising.
- Full suite at the end (Python 1050+ + TS 121).

## Risks

- PaperBroker balance/position tracking is new state — must stay consistent
  with the kernel's own Portfolio/Account read models (the sync reconciles to
  it, so a drift would surface as a sync reset — caught by Fix 1's test).
- Fix 3 changes live divergence behavior (fewer phantom exits) — the correct
  behavior per the review.
- Fix 4 changes multi-day backtest signals (morning session no longer sees
  yesterday's bars) — sanctioned; re-run zero-parity.
