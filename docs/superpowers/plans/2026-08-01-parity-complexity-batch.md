# G4 — Parity & Complexity Batch: live-fill charges, backtest candle fidelity, scanner keys, first-tick LTP, gate equity

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five audit findings tracked on the kanban board — B-006..B-009 and D-017:
- B-006 / F-004: live `BrokerExecution` fills carry zero commission/statutory → parity gap vs `SimulatedExecution`.
- B-007 / F-005: backtest candles degenerate (`open=high=low=close`) because `CandleEngine` ignores `QuoteEvent` OHLCV.
- B-008: scanners read indicator keys the pipeline never produces (`avg_volume`/`rsi`/`supertrend`) instead of `rsi_14`/`atr_14`/`stx_10_3` → 3 dead branches.
- B-009 / HF-001: `MarketEngine.on_tick` broadcasts read-model `_quote.ltp` instead of the tick's own `event.price`.
- D-017: `gate.py` re-derives equity/drawdown with divergent MTM vs `RiskEngine.equity`/`Position.market_value`.

**Baseline:** 616 passing tests. Test runner: `./.venv/bin/python -m pytest -q` (run from `/Users/apple/Downloads/nTrade`).

## Global Constraints

- Test command is always `./.venv/bin/python -m pytest -q`.
- Events stay `@dataclass(frozen=True, kw_only=True)` extending `ntrade.events.base.Event`; `ts` from the kernel clock, never `datetime.now()`.
- Domain layer stays broker-agnostic; no new third-party dependencies.
- Canonical indicator keys are `rsi_14`/`atr_14`/`vwap`/`stx_10_3`/`ema_9`/`ema_21` (compute_bundle lineage, asserted in `tests/test_indicators.py`). Scanners align to these — never the reverse.
- Each task's gate: failing test first, then implementation, then full suite green.
- `rg` is not installed in this shell — use `grep -rn` if a search is needed.
- Kanban cards B-006..B-009 and D-017 track each finding; sync via `python3 /Users/apple/.agents/skills/kanban.cli/scripts/kanban.py update` after completing tasks.

---

## Task Group 1 — B-006 / F-004: live fills pay commission + statutory

**Finding:** `BrokerExecution._emit_fill` (ntrade/execution/broker_executor.py:250-254) publishes `OrderFilledEvent(order_id, symbol, exchange, side, quantity, fill_price, strategy, ts)` with `commission` and `statutory` left at their event defaults of `0.0` (events/order.py:59-62). `SimulatedExecution` (ntrade/execution/simulator.py:103-143) computes both: `commission = round(self.commission.apply(notional), 4)` and, when `statutory is not None`, `model.total_cost(notional, side, brokerage=commission)` via `self.statutory.for_instrument(instrument)`. Live fills therefore cost nothing while paper/backtest fills charge — paper PnL never converges on live.

**Files:** `ntrade/execution/broker_executor.py`, `ntrade/kernel/session.py`, `tests/test_live_execution.py`, `tests/test_broker_executor.py`

- [ ] Add to `BrokerExecution.__init__` (broker_executor.py:52) the same cost args as `SimulatedExecution.__init__`:
      `commission: CommissionModel | None = None` and `statutory=STATUTORY_DEFAULT`.
      Store `self.commission = commission or FlatCommission(0.0)` and `self.statutory: IndianStatutoryCosts | None = resolve_statutory(statutory)`.
      Import from `ntrade.execution.costs`: `CommissionModel`, `FlatCommission`, `IndianStatutoryCosts`, `resolve_statutory`, `STATUTORY_DEFAULT`.
- [ ] In `_emit_fill` (broker_executor.py:255), after computing `price`, resolve the instrument via `self.ctx.instrument(intent.symbol)` and compute:
      `notional = price * new_qty`
      `commission = round(self.commission.apply(notional), 4)`
      `statutory = 0.0` if `self.statutory is None` else `round(self.statutory.for_instrument(instrument).total_cost(notional, intent.side, brokerage=commission), 4)`
      Pass both into the `OrderFilledEvent(...)`. Guard `instrument is None` → statutory falls back to `IndianStatutoryCosts()` default product schedule (or `0.0`), never crash.
- [ ] Thread the session's existing `statutory` param into the live target: kernel/session.py:93 `BrokerExecution(self.ctx, broker)` → `BrokerExecution(self.ctx, broker, statutory=statutory)` (session already defaults `statutory=STATUTORY_DEFAULT`, so live now charges by default exactly like the sim target).
- [ ] **Tests first, then impl:**
  - [ ] Failing test: live kernel with a synchronous-fill broker (reuse `_completed_order_status` / `make_broker` in tests/test_live_execution.py) emits `OrderFilledEvent` with `commission == 0.0` (FlatCommission default) and `statutory > 0.0` under default wiring — today it is `0.0`.
  - [ ] Failing test: `BrokerExecution(ctx, broker, commission=FlatCommission(5.0), statutory=None)` emits `commission == 5.0` and `statutory == 0.0` (zero-cost opt-out preserved).
  - [ ] **Update** `test_live_kernel_zero_parity_with_simulated` (tests/test_live_execution.py:168-198): it currently runs sim with `statutory=None` and live with the default. With the fix both sides must use the same statutory setting — pass `statutory=None` to the live kernel too (comment says "live broker pays no sim statutory"), so the parity assertions (balance, fill price/qty, position) remain meaningful and green.
  - [ ] Confirm `test_broker_executor.py` stale-eviction test and `test_findings_batch3.py` M2 tests still pass — they construct `BrokerExecution(ctx, broker)` with default args and assert on `open_orders()`, never on charge amounts.
- [ ] Full suite green: `./.venv/bin/python -m pytest -q`.

---

## Task Group 2 — B-007 / F-005: backtest candles carry real OHLCV

**Finding:** `CandleEngine` subscribes only to `TickEvent` (ntrade/engines/candle_engine.py:29) and ingests `(symbol, exchange, price, ts, volume=event.quantity)` (candle_engine.py:38-39). The backtest simulator publishes one `QuoteEvent` carrying the full OHLCV bar (ntrade/backtest/simulator.py:129) followed by one `TickEvent` at the close price (simulator.py:134-137). CandleEngine therefore sees a single price per bucket and builds degenerate candles (`open=high=low=close`). `QuoteEvent` carries `open/high/low/prev_close/volume/oi` (events/market.py:23-37); `CandleClosedEvent` carries `open/high/low/close/volume` (events/market.py:51-61) and feeds `IndicatorEngine` (indicator_engine.py:28-46) which needs ≥10 real candles to compute `rsi_14`/`atr_14`/`stx_10_3`.

**Design decision (verify in implementation):** only the **backtest** producer emits bar-shaped `QuoteEvent`s (`ctx.mode == "backtest"`, simulator.py:88). Live/replay `QuoteEvent`s carry day-session OHLCV (dhan_feed.py:72, market_feed.py:94) and must NOT be ingested as candle bars. Gate bar ingestion on `self.ctx.mode == "backtest"`, and skip the redundant close `TickEvent` that shares the bar's bucket so volume is not double-counted.

**Files:** `ntrade/engines/candle_engine.py`, `tests/test_kernel_engines.py`, `tests/test_replay_backtest.py`, `tests/test_candle_engine.py` (if present)

- [ ] `CandleEngine.__init__` also subscribes: `context.bus.subscribe(QuoteEvent, self.on_quote)` (import `QuoteEvent` from `ntrade.events.market`).
- [ ] New `on_quote(self, event)`: if `self.ctx.mode != "backtest"` → return. Otherwise ingest the bar authoritatively into the bucket: set `open=event.open, high=event.high, low=event.low, close=event.close, volume=event.volume` (not min/max accumulation), and record `self._bar_seeded[symbol] = bucket` so the paired close tick is skipped.
- [ ] `on_tick`: before ingesting, if `self._bar_seeded.get(event.symbol) == self._bucket(event.ts)` → skip (the simulator's close tick is the same bar's print; ingesting it would double the volume). Remove/ignore the seed mark once the bucket advances (or store a `(symbol, bucket)` last-seed key and compare buckets).
- [ ] **Tests first, then impl:**
  - [ ] Failing test: run `BacktestSimulator(timeframe="5m", initial_cash=100_000.0, statutory=None)` over `_ohlcv(20)` (tests/test_replay_backtest.py:120-130, per-bar `open/close` differ by 1.0 and `high/low` by 3.0) and assert the closed candles are NOT degenerate — e.g. via the kernel's `candle_engine.candles("NIFTY")` (or instrument `candles()` accessor) that `candle.open != candle.close` for bars after the first, `candle.high == bar_high`, `candle.low == bar_low`, and `candle.volume == 1000`. Today these are all `open=high=low=close` and volume `2000` (double-counted).
  - [ ] Failing test: with real candles flowing, `IndicatorEngine` eventually populates `instrument._indicators` with `rsi_14`/`atr_14`/`stx_10_3` after ≥10 bars.
  - [ ] Existing tick-only candle tests must stay green (`test_market_engine_projects_quote` in tests/test_kernel_engines.py, `test_candle_engine_closes_candle_on_next_bucket` L60, `test_candle_engine_flush_closes_partial` L75, `_tick` helper L22-24) — they run in `mode="replay"`, which the mode gate leaves untouched.
  - [ ] `test_backtest_simulator_produces_equity_curve` (test_replay_backtest.py:133) must still pass — `BuySellOnCandles` trades on candle-count and `event.close`, both unchanged by the fix.
- [ ] Full suite green: `./.venv/bin/python -m pytest -q`.

---

## Task Group 3 — B-008: scanners read canonical indicator keys

**Finding:** `compute_bundle` (ntrade/domain/analytics/indicators.py:148-190) emits `rsi_14`/`atr_14`/`vwap`/`stx_10_3`/`ema_9`/`ema_21` (and `sma_N` when configured) — never `avg_volume`, `rsi`, `supertrend`, or `atr`. The built-in scanners read the phantom keys:
- `VolumeSpikeScanner` reads `indicators.get("avg_volume", 0)` (ntrade/scanners/builtin.py:92) — dead branch.
- `MomentumScanner` reads `indicators.get("rsi")` (builtin.py:131) — dead branch.
- `BreakoutScanner` reads `indicators.get("supertrend")` (builtin.py:174) and `indicators.get("atr")` (builtin.py:201) — dead branches.

**Files:** `ntrade/scanners/builtin.py`, `ntrade/domain/analytics/indicators.py`, `tests/test_scanner.py`, `tests/test_indicators.py`

- [ ] `MomentumScanner`: read `rsi_14` (canonical; the IndicatorEngine default `rsi_period=14`). Keep the price-change fallback unchanged.
- [ ] `BreakoutScanner`: read `stx_10_3` instead of `supertrend`; keep the high/low fallback. For the `indicator_values` summary, read `atr_14` instead of `atr`.
- [ ] `VolumeSpikeScanner`: the pipeline has no average-volume key. **Add `avg_volume` to `compute_bundle`** (mean of the `volume` column over the window, e.g. `float(df["volume"].mean())`) so the spike-multiplier branch is genuinely reachable; keep the absolute-`min_volume` fallback. (Alternative if the maintainer prefers: delete the `avg_volume` branch and rely on the absolute fallback — but emitting the key keeps the documented spike feature working.)
- [ ] **Tests first, then impl:**
  - [ ] Update `tests/test_scanner.py` fixtures (L221-276) from phantom keys to canonical: `{"avg_volume": 100_000}` stays (now produced), `{"rsi": 72.0}` → `{"rsi_14": 72.0}`, `{"supertrend": 1650.0}` → `{"stx_10_3": 1650.0}`. Assert each scan still returns the expected single result and condition.
  - [ ] Add `"avg_volume" in bundle` to `test_compute_bundle` (tests/test_indicators.py:67-72).
- [ ] Full suite green: `./.venv/bin/python -m pytest -q`.

---

## Task Group 4 — B-009 / HF-001: on_tick broadcasts the tick's own price

**Finding:** `MarketEngine.on_tick` (ntrade/engines/market_engine.py:25-37) publishes `QuoteUpdatedEvent(ltp=instrument._quote.ltp, ...)` — a read of the instrument's mutable quote after `ingest_tick`, not the tick's authoritative `event.price`. Empirically verified: the broadcast is correct for `trade`/`quote` kinds only because `ingest_tick` mutates `_quote.ltp` first; for a `depth`-kind tick the broadcast is `0.0` (LiveStream.ingest_tick, ntrade/domain/market/stream.py:106-122, only updates `_quote` for `quote`/`trade`). This ordering dependence is fragile and wrong by construction — the tick event owns the price.

**Files:** `ntrade/engines/market_engine.py`, `tests/test_kernel_engines.py`

- [ ] `on_tick`: broadcast `ltp=event.price` (the tick's own price), not `instrument._quote.ltp`. Bid/ask can stay `instrument._quote.bid/ask` (best-effort snapshot).
- [ ] **Tests first, then impl:**
  - [ ] Failing test: publish a `TickEvent(..., kind="depth")` and assert the emitted `QuoteUpdatedEvent.ltp == event.price` (today it is `0.0`).
  - [ ] Existing `test_market_engine_projects_tick` (tests/test_kernel_engines.py:27) still passes — default kind is `trade`, `event.price == 2500.5`.
- [ ] Full suite green: `./.venv/bin/python -m pytest -q`.

---

## Task Group 5 — D-017: gate.py equity derives from the portfolio read model

**Finding:** `gate.py::_equity_trace` (ntrade/runner/gate.py:10-34) re-derives equity from raw `OrderFilledEvent` (cash) + `TickEvent`/`QuoteEvent` (LTP mark) — a third owner of the money state. `RiskEngine.equity` (ntrade/engines/risk_engine.py:44-47) = `account.balance + Σ Position.market_value`, and `Position.market_value = round(quantity * ltp, 2)` (domain/portfolio.py:30-31) where `ltp` is set at fill (portfolio_engine.py:27/46) or broker sync (position_sync.py:56) — never from raw quotes. The gate's LTP mark therefore diverges from the portfolio read model. Fix: the gate must consume the canonical `PositionUpdatedEvent` + `BalanceChangedEvent` stream (published by `PortfolioEngine`, portfolio_engine.py:60-70) so its equity equals `RiskEngine.equity` at every step.

**Files:** `ntrade/runner/gate.py`, `tests/test_paper_gate.py`

- [ ] Rewrite `_equity_trace` to reconstruct from the portfolio read-model events in `kernel.bus.history`:
  - [ ] Start `cash = float(initial_cash)`; keep a `positions: dict[str, Position]`-style map (symbol → quantity/ltp).
  - [ ] On `PositionUpdatedEvent` (events/portfolio.py:13-20: `symbol/quantity/avg_price/ltp`): update the map (quantity 0 → drop).
  - [ ] On `BalanceChangedEvent` (events/portfolio.py:25-27: `balance`): set `cash = event.balance`.
  - [ ] `eq = cash + sum(q * ltp for ...)`; track `peak`, yield `(peak, eq)` — identical semantics to today but on the same MTM basis as `RiskEngine.equity`.
  - [ ] Remove the `TickEvent`/`QuoteEvent` LTP mark and the fill-by-fill cash math. Drop now-unused `QuoteEvent`/`TickEvent` imports from gate.py.
- [ ] `build_paper_report` (gate.py:37+) keeps reading `OrderFilledEvent` for `fills`/`total_charges` — unchanged. `final_equity` already uses `ctx.portfolio` + `account.balance` (gate.py:49-54); leave as-is (it matches `RiskEngine.equity`).
- [ ] **Tests first, then impl:**
  - [ ] Failing test: after a replay run with one BUY fill at `100.0`, `build_paper_report(...)` `max_drawdown_pct` is consistent with the portfolio read model — assert `report["final_equity"]` equals `kernel.risk_engine.equity()` to 2dp.
  - [ ] Existing `test_paper_gate.py` assertions stay green: `fills[0].statutory > 0.0` (default statutory wiring on the sim target), `trade["commission"] == fills[0].commission`, `checklist["total_charges"]` sum, and the empty-run `final_equity == 100_000.0` (no BalanceChangedEvent/PositionUpdatedEvent → trace yields `(100000, 100000)`).
- [ ] Full suite green: `./.venv/bin/python -m pytest -q`.

---

## Definition of Done (whole batch)

- [ ] All five task groups implemented with failing-test-first discipline.
- [ ] `./.venv/bin/python -m pytest -q` → 616+ passing, no regressions.
- [ ] Kanban cards moved to done (`kanban.py update`); `.superpowers/sdd/progress.md` updated per task.
- [ ] Commit per task group with the repo's message style (see `git log --oneline -10`).
- [ ] Final review pass across the batch (request a review for the aggregate diff).
