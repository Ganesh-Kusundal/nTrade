# nTrade Multi-Agent Quant Review — Consolidated Findings

> Compiled 2026-08-12 from 4 parallel review agents: (1) Fabio-strategy fidelity,
> (2) kernel/event/risk/flows + zero-parity, (3) TS-mirror duplication/parity,
> (4) repo-wide smells/duplication. Read-only reviews; one fix already applied.

## Priority 0 — ALREADY FIXED during this review

| Finding | Severity | Fix |
|---|---|---|
| TS mirror SELL runner R:R: `(entry-(sl-entry)*tpMult)/(sl-entry)` = **18.0** instead of 2.0 (valentini.ts:526) — defeated `minRr` for SELL runners, reported R:R garbage. Python (strategies.py:663) is correct. | Critical (parity) | **cf73f67** — fixed formula, locked BUY runner R:R to tp_multiplier in tests. 121 TS tests pass. |

## Priority 1 — CRITICAL (fix next)

### Runtime crashes in shipped strategies (repo-smell agent)
1. **`GainzCloneStrategy.__init__` — `strategies.py:827`**: `datetime.strptime(...).time` missing `()` → bound to the method → `TypeError` on first in-session candle. `scripts/backtest_gainz_clone.py:124` guaranteed crash.
2. **`hma` / `rsi` never imported** (`strategies.py:16` imports only `atr, vwap, vwap_bands`) but `GainzCloneStrategy` calls `hma()` (862) / `rsi()` (867) and `VwapReclaimStrategy` calls `rsi()` (1016) → `NameError`. **Both strategies have zero tests.**

   → These are the uncommitted WIP (`GainzCloneStrategy` + `wma` were in the working tree, not from this batch). They're broken and untested; recommend delete or fix+test, but NOT ship as-is.

### Engine/flow bugs (kernel agent)
3. **Paper/synth runs wipe kernel state every sync** — `PositionSyncEngine` (wired unconditionally for any broker) treats PaperBroker's `get_positions()=[]`/static balance as truth and **drops every position + resets balance to 1,000,000 every 15s** (`position_sync.py:66-80`). Corrupts sizing, loss breakers, and paper-vs-backtest parity.
4. **Backtest results truncated by 10k-event bus cap** — `BacktestSimulator.results()` rebuilds trades/costs from `bus.history` which is a `deque(maxlen=10_000)`; a 30-day run (~45k events) silently drops the first 75% → **wrong n_trades / win-rate / costs** in `scripts/backtest_valentini.py`.
5. **Live circuit breakers never evaluate continuously** — `LiveRunner._evaluate_risk` calls the *paused global* engine (no caps); per-strategy `max_daily_loss`/`max_drawdown_pct` only trip at the next signal. Backtest enforces them every bar → **live silently stops enforcing loss caps**.

## Priority 2 — IMPORTANT (strategy + parity)

### Strategy fidelity (Fabio agent)
6. **Divergence exit benchmark is dimensionally broken** (`strategies.py:331` vs `647-648`): compares ONE range-bar's volume to the WHOLE entry-leg SUM × 0.6 → fires on nearly every higher-high range bar → **the auction-following runner becomes a 1-2 bar scalp**. Compare to a per-bar benchmark instead.
7. **Session rollover never clears `_rows`** (`strategies.py:443-454`): profile/VWAP/bands/direction built over prior-day bars until the 600-row window rolls them out (~3.8h into each day). Morning location/SL/direction corrupted.
8. **Direction gate is weak**: volume vote is leg-SUM vs per-bar-MEDIAN (vacuous for multi-bar legs, `strategies.py:281`); swing vote is a 2-bar local test not confirmed pivots; direction is re-derived on the trigger candle, not "established first" (`strategies.py:583`).
9. **Entry lands near VAH, stop at VAL−step → ~3-4 ATR stop** — violates Fabio's tight-invalidation/small-risk premise (`strategies.py:561,583-599,653-654`).
10. **Reversal near-dormant**: extension measured vs the current leg's OWN POC (`strategies.py:370,378`); no R:R gate on fades; SL anchored at absorption close not its extreme.

### TS mirror (TS agent)
11. **Static step/auto-range lookahead** (`valentini.ts:168-183`): step + auto rangeSize frozen at the final bar's ATR for the whole window; Python recomputes per-bar → every step-scaled threshold, bucket width, and range-bar series diverge on multi-day data.
12. **Per-session VWAP (TS) vs window-cumulative (Python `strategies.py:471`)** on multi-day data.

## Priority 3 — MINOR / DRY / cleanliness

- **Duplication**: `_manage_exit`+`_exit` byte-identical across GainzClone & VwapReclaim (mixin candidate); IST-normalize blocks ×4; session-VWAP ×4; `_INTERVAL_MINUTES`/`_SPAN_MIN` duplicate dicts; `_load_local`/`_metrics` ×3 in scripts; TS full-strategy mirror (deliberate, but 3rd drift cycle).
- **Dead code**: `register_default_brokers`, `CvdTracker`, `OptionFactory`, `HistoryStorage` protocol — all test-only.
- **Test smells**: tautological asserts (`test_factories_facade.py:37`), trivial `>= 0` asserts, on-disk-parquet-coupled tests with hardcoded dates, Dhan test-stub duplication across 6 files.
- **Inconsistencies**: `OrderSide(str, Enum)` vs `StrEnum`; session-boundary inclusive-vs-exclusive across 3 layers; `1d` vs `1D` timeframe; `"NSE"` strings vs `Exchange.CASH`.
- **`ponytail:` comments** (24×) — tracked-debt mechanism; ledger, don't delete.

## Highest-value fixes (ordered)

1. Fix paper position-sync wipe (A1/P3) — one-line guard for PaperBroker.
2. Build backtest results from a dedicated fill list, not the capped bus history.
3. Fix divergence-exit benchmark (per-bar, not leg-sum).
4. Clear `_rows` at session rollover (session-anchor the analytics).
5. Fix the 2 GainzClone/VwapReclaim crashes or delete the broken WIP strategies.
6. Make the direction gate stateful + like-for-like volume comparison.
7. Route live risk to per-strategy engines (continuous loss-cap enforcement).
8. TS: recompute step per bar (remove lookahead); add a cross-language conformance test.

## What's healthy (verified)
- Risk gate is a true single choke point (OrderEngine ← SignalApprovedEvent only; no bypass).
- `market_hours.py` is the single source of truth for sessions (strategies derive from it).
- Zero-parity invariant design is real and mostly enforced; the divergences above are the gaps.
- Event bus is deterministic, serialized, RLock-safe.
- The 6 recently-ported TS features are in parity except items 11-12.
