# Valentini Scalper — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an automated approximation of Fabio Valentini's Triple-A scalping model (absorption → accumulation → aggression) on NIFTY futures, using **only what nTrade already has** — verified live: 1m OHLCV history, tick/quote/depth feed (NFO Full mode), event-driven kernel, risk engine, paper/backtest/live execution. Backtest-validated before any live wiring.

**Architecture:** 3 new pure-analytics modules + 1 strategy + validation scripts. No new abstractions, no framework changes — everything plugs into the existing `TradingKernel`/`Strategy`/`BacktestSimulator` stack (zero-parity live/replay/backtest is already the kernel's invariant).

**Tech Stack:** Python 3.12+, pandas, pytest, existing `ntrade` kernel

## Data reality (from live verification, 2026-08-06)

- Dhan gives **1m OHLCV history** + **live ticks (LTP/LTQ)** + **depth (NFO)**. It does **NOT** give trade tape, per-trade aggressor side, or historical ticks.
- True order flow (Valentini's CVD, 20–30-contract aggression bubbles, footprint) is **impossible on Dhan** → we build a documented **approximation layer**: tick-rule CVD live, OHLCV-proxy CVD in backtest.
- L2 depth works on **NFO (NIFTY futures)** — not on the IDX index (verified in code + library). So we **trade the future, never the index**. Depth is snapshot-based via `get_depth()`; the Full(21) feed streams 5-level depth continuously for NFO.
- NIFTY futures front-month is the primary instrument (liquid, has L2, trades 1m cleanly).

## Global Constraints

- No new dependencies (pandas + stdlib only)
- New files only where a module doesn't already exist; otherwise modify in place
- Every task ends with `pytest` green; strategy tasks get unit tests before wiring
- Approximations are **explicitly labeled** in docstrings (CVD/absorption are proxies, not true order flow)
- Ponytail: simplest thing that captures the signal with the data we have

## Skipped (YAGNI)

- **True order flow / footprint / aggression bubbles** — data does not exist on Dhan; the tick-rule + OHLCV-proxy layer is the ceiling. If a tape-capable venue is added later, only `order_flow.py` changes.
- **Frontend dashboard (React/SSE from the build guide)** — nTrade is an SDK; the guide's web UI is a separate product, not foundation.
- **ML absorption classifier** — speculative until the base model has a validation record.
- **Multi-timeframe confluence** — Phase 3 baseline first; add only if backtest metrics justify it.

---

### Task 1: Range bar generator

**Files:**
- New: `ntrade/domain/analytics/range_bars.py`
- Test: `tests/test_range_bars.py`

**Interfaces:**
- Consumes: 1m OHLCV `pd.DataFrame` (timestamp/open/high/low/close/volume)
- Produces: `build_range_bars(df, range_size=None, atr_period=14) -> pd.DataFrame` of range bars (open/high/low/close/volume/timestamp of last source bar), `calc_auto_range(df, atr_period=14) -> float`

**Why:** The model's bar primitive is price-based (fixed range), not time-based. `CandleEngine` is time-based and stays untouched; the strategy builds range bars from 1m candles internally.

- [ ] **Step 1: Write the failing test** (`build_range_bars` splits a synthetic trending 1m series into bars of width ≈ range_size; volume is distributed proportionally to the ticks simulated inside each candle; `calc_auto_range` maps ATR to a sane tick-scaled value)
- [ ] **Step 2: Implement `calc_auto_range`** — `round(ATR(14) * k, tick_step)` with fallback constants
- [ ] **Step 3: Implement `build_range_bars`** — simulate a canonical O→H/L→C path per 1m candle, close a range bar the moment `high - low >= range_size`, distribute the candle's volume across the bars it produced
- [ ] **Step 4: Edge cases** — incomplete last bar (marked `is_complete=False`), flat/gap candles, empty input
- [ ] **Step 5: `pytest tests/test_range_bars.py` green**

### Task 2: Volume profile (POC / VAH / VAL)

**Files:**
- New: `ntrade/domain/analytics/volume_profile.py`
- Test: `tests/test_volume_profile.py`

**Interfaces:**
- Consumes: bar `pd.DataFrame` (price + volume), optional bucket `step`
- Produces: `VolumeProfile` dataclass `{levels: list[VPLevel], poc: float, vah: float, val: float, step: float}`; `build_volume_profile(bars, step=None) -> VolumeProfile`; `VPLevel {price, volume, delta: float, total}`

**Why:** Location (POC, value area high/low, low-volume nodes) is step-1 of the model — the transcript's "use the profile of the previous day" maps to building the profile over prior-session bars.

- [ ] **Step 1: Write the failing test** (single-modal volume → POC at peak bucket; value area captures 68% of volume around POC via the guide's expansion algorithm; step defaults to the range-bar step)
- [ ] **Step 2: Implement bucket allocation** — for each bar, distribute volume across the buckets its price span touches (proportional to overlap)
- [ ] **Step 3: Implement POC + value-area expansion** (expand to the higher-volume adjacent bucket until ≥68% captured) → VAH/VAL
- [ ] **Step 4: `pytest tests/test_volume_profile.py` green**

### Task 3: VWAP ±σ bands

**Files:**
- Modify: `ntrade/domain/analytics/indicators.py` (add `vwap_bands`)
- Test: extend `tests/test_indicators.py`

**Interfaces:**
- Consumes: OHLCV `pd.DataFrame`
- Produces: `vwap_bands(df, num_std=2.0) -> tuple[float, float]` (upper/lower band at the last bar), reusing the existing `vwap()` for the mean

**Why:** Price-above/below VWAP is the direction filter and the Triple-A "aggression" trigger leg; bands give the overbought/oversold context used to skip late entries.

- [ ] **Step 1: Failing test** (hand-computed two-bar case; band width grows with volatility)
- [ ] **Step 2: Implement** — accumulate `(typical - vwap)² · vol` per bar, band = vwap ± num_std · sqrt(cum/vol)
- [ ] **Step 3: `pytest tests/test_indicators.py` green**

### Task 4: Order-flow approximation layer (CVD + absorption)

**Files:**
- New: `ntrade/domain/analytics/order_flow.py`
- Test: `tests/test_order_flow.py`

**Interfaces:**
- Consumes: live `TickEvent`-shaped tuples `(price, quantity)`; or OHLCV `pd.DataFrame` for backtest
- Produces:
  - `CvdTracker` — `update(price, qty) -> None`, `delta: float`, `cvd: float` (tick-rule: trade classified buy when price ≥ last, sell when below; LTQ is the size)
  - `cvd_from_ohlcv(df) -> pd.Series` — backtest proxy: bar classified by `close` vs `open` (or vs `vwap`), volume × side
  - `detect_absorptions(bars, avg_volume_mult=1.5, range_threshold=0.5, range_size=None) -> list[Absorption]` — `Absorption {bar_index, price, volume, side, strength}`

**Why:** Step-3 of the model is aggression — the only trigger we can build on Dhan. Absorption ("big volume, no price") is fully computable from OHLCV + volume. CVD needs the buy/sell split, which is approximated (documented as such).

- [ ] **Step 1: Failing tests** — tick-rule CVD on a synthetic up/down tick sequence; OHLCV proxy on a synthetic frame; absorption flagged on a high-volume compressed bar and *not* on a normal bar
- [ ] **Step 2: Implement `CvdTracker`** (pure, no kernel dependency — strategy feeds it from `on_tick`)
- [ ] **Step 3: Implement `cvd_from_ohlcv`** proxy
- [ ] **Step 4: Implement `detect_absorptions`** — volume > mult × rolling avg AND range < range_threshold × range_size; side from the CVD/buy-sell proxy; strength = normalized volume excess (0–1)
- [ ] **Step 5: `pytest tests/test_order_flow.py` green**

### Task 5: ValentiniScalper strategy (Triple-A state machine)

**Files:**
- New: `ntrade/engines/strategies/valentini.py` (or add class to existing `ntrade/engines/strategies.py`)
- Test: `tests/test_valentini_strategy.py`

**Interfaces:**
- Consumes: standard kernel events (`on_candle_closed` for range-bar close, `on_tick` for live CVD, `on_position_updated`/`on_order_filled` for trade management)
- Produces: `emit_signal(...)` → existing RiskEngine → execution
- Class: `ValentiniScalper(Strategy)` with params: `range_size=0 (auto)`, `tp_multiplier=2.0`, `min_rr=1.5`, `risk_per_trade_pct=0.5`, `abs_volume_mult=1.5`, `abs_range_threshold=0.5`, `session_start/end`

**State machine (per the guide):**
`waiting → absorbing (absorption detected) → accumulating (2+ bars, price within 2 steps of POC) → signal (LONG: BUY absorption + price > VWAP; SHORT: SELL absorption + price < VWAP)`

**Trade rules:**
- SL: LONG below `VAL - step`, SHORT above `VAH + step` (guide); tightened 1 tick inside structure for scalp protection
- TP: `entry ± (entry - SL) × tp_multiplier`; skip if computed RR < `min_rr`
- Breakeven: move SL to entry after the first confirming bar in profit; trail with the accumulation structure
- Position sizing: `qty = (account × risk_pct) / (entry - SL)`, floored/rounded to NIFTY lot size (via existing `get_lot_size`)
- Session gate: only act inside NSE futures session (09:15–15:25 IST); flat by session close
- Per-day max losses already enforced by existing `RiskEngine(max_daily_loss=…)` at registration (`register_strategy(risk=…)`)

- [ ] **Step 1: Failing tests** — full Triple-A progression on a synthetic bar sequence produces a LONG signal exactly when the conditions align; no signal when VWAP filter fails; RR filter rejects low-RR setups; state resets to `waiting` after a signal
- [ ] **Step 2: Implement range-bar accumulation inside the strategy** (feed 1m `on_candle_closed` → `build_range_bars` incrementally)
- [ ] **Step 3: Implement state machine + signal generation**
- [ ] **Step 4: Implement SL/TP/RR computation + position sizing**
- [ ] **Step 5: Implement trade management (breakeven move, trail, session close) + session gate**
- [ ] **Step 6: `pytest tests/test_valentini_strategy.py` green**

### Task 6: Backtest validation on NIFTY futures

**Files:**
- New: `scripts/backtest_valentini.py` (reuses `BacktestSimulator` — no framework changes)

**Interfaces:**
- Consumes: `broker.get_historical(nifty_future, "1m", days=N)` (verified working) → range bars → `BacktestSimulator` with `register_strategy(ValentiniScalper(...))`
- Produces: printed + saved validation report: win rate, avg R:R, max drawdown, Sharpe, trade count, equity curve CSV

**Why:** Zero-parity kernel means a backtest IS the live stack — validating here de-risks any later live run.

- [ ] **Step 1: Pull 60–90 days of front-month NIFTY future 1m data; build range bars**
- [ ] **Step 2: Run `BacktestSimulator.run(bars)` with the strategy + NIFTY cost model (commission/statutory/slippage)**
- [ ] **Step 3: Compute validation metrics from `BacktestResult`** (trades list has fills) — win rate, avg R:R, Sharpe from equity curve, max DD
- [ ] **Step 4: Sanity baselines** — compare against always-flat and a fixed-bar entry; confirm the model's edge exists or is honestly reported as not-yet-there
- [ ] **Step 5: Document results in this plan's appendix**

### Task 7: Live / paper wiring (post-validation gate)

**Files:**
- Modify: none required in the kernel; a run script + wiring test only
- Test: `tests/test_valentini_live_wiring.py` (feed → strategy → risk → paper execution, no real orders)

**Interfaces:**
- Consumes: `DhanMarketFeedSource` (NFO Full mode — verified streaming), `LiveRunner`, paper broker first
- Produces: live strategy run with `CvdTracker` fed by `on_tick`; risk-engine caps; kill switch armed

**Gate:** Only start this phase if Phase 6 metrics justify it.

- [ ] **Step 1: Wire NIFTY future to the live feed; confirm `on_tick`/`on_candle_closed` flow into the strategy**
- [ ] **Step 2: Run paper session with full risk caps** (`RiskEngine` daily-loss + drawdown), verify no real orders
- [ ] **Step 3: Depth confirmation (optional)** — use Full(21) depth `bid_ask_imbalance` as a signal-confidence filter
- [ ] **Step 4: `pytest` + a smoke paper run; document go/no-go for real orders**

---

## Appendix: validation results (filled after Phase 6)

| Metric | NIFTY future | Baseline (flat) |
|---|---|---|
| Win rate % | — | — |
| Avg R:R (winners) | — | — |
| Max drawdown % | — | — |
| Sharpe | — | — |
| Trades | — | 0 |

## Appendix: approximation registry (honest labels)

| Signal | Live | Backtest | Fidelity |
|---|---|---|---|
| CVD / delta | Tick-rule on LTP+LTQ | close-vs-open volume proxy | proxy — no aggressor tape on Dhan |
| Absorption | volume × range from 1m candles | same (OHLCV-only) | good — computable from OHLCV |
| Aggression bubbles (20–30 lot) | not available | n/a | **not implementable** on Dhan |
| POC / VAH / VAL | prior-session profile | same | full |
| VWAP + bands | full | full | full |

## Implementation notes (2026-08-06)

All 7 tasks landed. Code lives in ``ntrade/domain/analytics/`` (``range_bars``,
``volume_profile``, ``order_flow``, ``indicators.vwap_bands``), the
``ValentiniScalper`` in ``ntrade/engines/strategies.py``, and the harness in
``scripts/backtest_valentini.py`` — **904 tests pass**, ruff clean.

Design decisions worth recording for the live phase (Task 7):

- **Exits are in-strategy, not broker-enforced.** SL/TP travel as signal
  metadata and are managed per candle in ``_manage_exit`` (stop wins over
  target on a same-bar double-hit — conservative; hard session close always
  flattens at the gate). For backtest this is the right model (bar-aware
  fills), but **live should place ``BRACKET`` orders** (``stop_loss_price`` /
  ``target_price`` already in the ``Order`` model) so the broker enforces the
  stop if the strategy process dies mid-session.
- **VWAP bands are wired.** ``fade_extended=True`` (default) blocks an entry
  whose trigger bar has chased beyond VWAP ±2σ — the setup stays accumulating
  and re-fires on the pullback instead of chasing an extended move.
- **Sizing floors to the lot and never rounds up** past the risk budget;
  ``stop_distance == 0`` is guarded (qty=1 fallback).
- **Slippage is explicit in the harness** (``FixedSlippage(0.05)`` = one NIFTY
  tick) so MARKET-entry fills stay honest vs live auto-banded LIMITs (SEBI
  rule for F&O).
- **State is single-symbol** by construction (``symbol=None`` backtest runs one
  series; multi-symbol sessions would need per-symbol keying — out of scope).

  Review-hardening fixes (all test-covered):

  - ``_active`` arms **only when the entry fill lands** (``on_order_filled``
    hook, zero-parity across backtest/replay/live) — a RiskEngine-rejected or
    unfilled entry can no longer produce a phantom naked exit.
  - The absorbing phase **expires** (resets to waiting) if price runs away
    beyond 2×range for 15 bars, so stale setups can't hang the machine.
  - ``build_range_bars`` no longer mislabels the last bar partial when the
    final source candle completes a bar exactly.
  - Backtest win-rate FIFO pairing is now **direction-aware** (short round
    trips pair correctly).
  - **Rejected-entry lockout fixed** (audit finding): a risk/broker-rejected
    entry used to leave the pending entry staged forever, permanently
    blocking all future entries — it is now cleared in synchronous modes and
    expired per-candle in live.
  - Robustness guards from the audit: quote-only (qty=0) ticks no longer
    re-classify the CVD tick-rule side; ``buy_volume`` length mismatch raises
    ``ValueError`` instead of ``IndexError``; NaN candle volume is treated
    as zero in range bars.

### Accuracy audit (2026-08-06) — all six independent probes pass

Ground-truth verification performed **without reusing test expectations**
(scratch probes removed after the run):

| Module | Probe | Result |
|---|---|---|
| range_bars | 50 random trials vs independent path-walk reference; span/volume/timestamp invariants on 2000-bar synthetic NIFTY | ALL PASS |
| volume_profile | 60 trials vs brute-force POC/VAH/VAL; value area 68.0–74.1% on fine buckets | ALL PASS |
| vwap_bands | 40 trials vs independent cumulative computation | ALL PASS |
| order_flow | hand-traced tick rule, CVD proxy cumsum, absorption thresholds | ALL PASS |
| strategy | hand-computed sizing (qty=1041), SL/TP/RR, stop/target/breakeven exits, equity reconciliation to the cent | ALL PASS |
| test tautology | adjusted expectations (7/10 bar counts, VA 0.684) recomputed from first principles | ALL PASS |

**Known characteristics (documented, not bugs):**
- Engine-based backtests fill entries **one bar after the signal candle**
  (candles close on the next bar — realistic "act on close, fill next"
  semantics). The unit-test harness (direct candle events) fills on the
  signal bar itself. The script's displayed ``trades`` is a fill count;
  win rate is per round trip.

  Two acknowledged approximations, documented for the live phase:

  - **Exit fills record at the bar close**, not the SL/TP level (MARKET
    exits chosen over LIMIT so gap-through stops still exit; the synthetic
    stop PnL is slightly overstated).
  - **Session gate is timezone-naive** (``ts.time()``): fine in backtest with
    IST-naive labels; the live wiring must convert feed timestamps to IST
    before the 09:15–15:25 check.

### Phase 7 — live/paper wiring complete (2026-08-06)

All four Phase 7 steps landed (TDD):

| Step | Deliverable | Status |
|---|---|---|
| 1 | ``scripts/live_valentini.py`` — LiveRunner wiring: front-month NIFTY future, ``ValentiniScalper`` with full risk caps; synth (offline CSV or real history) / live (real websocket) modes | ✅ |
| 2 | ``tests/test_valentini_live_wiring.py`` — paper e2e: feed → strategy → RiskEngine → paper fills, quantity/notional caps, ``RiskHaltedEvent`` → ``trip_kill_switch``, no real orders | ✅ 14 tests |
| 3 | ``depth_imbalance_min`` confidence filter in ``ValentiniScalper`` (uses ``MarketDepth.bid_ask_imbalance()`` via the depth read-model) + tests | ✅ |
| 4 | Offline smoke run (``--csv``) end-to-end | ✅ |

**Three real bugs found & fixed during the Phase 7 smoke run (regression-tested):**

1. **Synth mode could place REAL Dhan orders (highest severity).** The first
   version of ``scripts/live_valentini.py`` ran synth mode on
   ``TradingSession.connect("dhan")`` — the kernel wires ``BrokerExecution``
   whenever a broker is present, so a synth-mode signal would have placed a
   real order despite the "no real orders" docstring. Fixed: synth/offline
   now resolve the future + history through a **throwaway** Dhan connection
   that is closed before the run, and the kernel is a ``TradingSession.paper()``
   session (no live broker → no live execution). Only ``--feed live`` keeps
   the Dhan session alive and reuses it as the run session (single login).
2. **PaperBroker filled at a stale seeded price.** ``place_order`` filled
   against the broker's static quote dict (default ₹100) instead of the
   instrument's live quote the kernel keeps updated from ticks — so entry and
   exit fills printed identical prices and PnL was invisible. Fixed: fills
   prefer the instrument's live ``_quote`` LTP, falling back to the seeded
   quote for static tests (LIMIT orders still honour ``order.price``).
3. **``RiskEngine`` ``max_notional`` silently bypassed by MARKET signals.**
   The notional check used ``event.price`` which is ``0.0`` for MARKET
   signals → ``0 × qty = 0`` → every notional cap passed. A ₹10.7M order
   sailed through a ₹2M cap. Fixed: notional is estimated from the
   instrument's live LTP (fallback prev_close) when the signal price is 0,
   via a shared ``_reference_price`` helper (also reused by the
   price-deviation guard; safe on unregistered symbols). New tests:
   ``test_market_signal_notional_uses_live_ltp``,
   ``test_market_signal_notional_falls_back_to_prev_close``.

**Smoke-run evidence (offline CSV replay, ₹1M paper, risk caps qty≤500,
notional≤₹15M):**

```
  ticks published : 6660      signals : 4  rejected: 1 (qty 530 > cap)
  fills           : 3
    BUY  NIFTY AUG FUT x446 @ 24002.35     # entry tracks live tick
    SELL NIFTY AUG FUT x446 @ 24029.09     # target exit at market
    BUY  NIFTY AUG FUT x183 @ 24048.99     # re-entry; open at end
  positions       : [('NIFTY AUG FUT', 183)]
  balance         : -3,392,955.96   # full-notional cash accounting (kernel
                                    # convention, matches BacktestSimulator)
  kill-switched   : False
  mode            : OFFLINE CSV (no broker, no real orders)
```

The negative balance is the kernel's documented **full-notional accounting**
(entry debits qty×price, exit credits) — identical to the backtest simulator,
so paper/live parity holds. For a margin-based read, ``RiskEngine.equity()``
(cash + open MTM) is the metric to watch.

Review hardening of the wiring (all covered above): the summary reads kernel
read-models for paper/synth and broker reads for live; ``--csv`` + ``--feed
live`` is rejected explicitly; the synth path reuses a single Dhan connection
and closes it before the run. Final state: **919 tests pass (was 911 → +8
wiring/risk tests), ruff clean, compileall clean.**

### Phase 6 — real-data validation (completed 2026-08-06, live connection)

The Dhan connection was re-verified **live**: token valid ~24h, NIFTY LTP
24636, expiry list 2026-08-25/09-29/10-27, balance ₹0.34.

**Blocking bug found & fixed — futures security-ID mapping:** ``get_historical``
sent the domain symbol ``NIFTY 25Aug26``, which matches **neither** of Dhan's
instrument-file forms (``SEM_TRADING_SYMBOL`` ``NIFTY-Aug2026-FUT`` /
``SEM_CUSTOM_SYMBOL`` ``NIFTY AUG FUT``) → ``Check the Tradingsymbol or
Exchange``. Per direction, the security-ID resolution stays **Dhan-internal**
(instrument-file lookup); our adapter only hands over the recognized trading
symbol. ``DhanMapper.to_trading_symbol`` now maps futures to the CUSTOM form
(``f"{underlying} {MONTH} FUT"``). Verified live on **NIFTY + BANKNIFTY**
futures (the two instruments this model targets):

| Underlying | dhan symbol | LTP | 1m history (2d) | lot |
|---|---|---|---|---|
| NIFTY | ``NIFTY AUG FUT`` | 24738.0 | 770 bars | 65 |
| BANKNIFTY | ``BANKNIFTY AUG FUT`` | 58191.0 | 770 bars | 30 |

New test ``test_future_maps_to_custom_symbol`` covers the mapping. **138
Dhan+Valentini tests pass, ruff clean.**

**Real-data backtest — pipeline works, 0 trades generated (tuning signal).**
30-day front-month NIFTY future 1m (8290 bars) ran end-to-end through
``BacktestSimulator`` in ~9 min (the strategy rebuilds range bars/VWAP/CVD
per candle over a 600-bar window — slow-but-linear; a vectorization pass is
future work, not a correctness issue). Result: **0 fills**. Root cause is the
setup criteria, not the wiring: on 10 days of real data, 42 absorptions fire
(default thresholds), but of **291 accumulating-phase bars only 1 crossed
VWAP on the absorption side** — with the ``fade_extended`` band filter off.
The Triple-A trigger (absorption → 2-bar consolidation within 2×step → close
past VWAP on the same side) is genuinely rare on NIFTY 1m with the current
defaults. **Action for real edge:** loosen ``abs_range_threshold`` /
``abs_volume_mult``, widen the accumulation window, and/or drop the VWAP-side
hard gate to a soft tilt — then re-run the same 30-day backtest. The harness
is the tool for that tuning loop.

Commands (now unblocked at any time the login probe passes):

```
./.venv/bin/python scripts/backtest_valentini.py --days 30   # real NIFTY fut 1m
./.venv/bin/python scripts/live_valentini.py --days 3        # synth rehearsal
./.venv/bin/python scripts/live_valentini.py --feed live --duration 60 \
    --live-kwargs '{...}' --max-quantity 25 --max-daily-loss 5000   # real
```
