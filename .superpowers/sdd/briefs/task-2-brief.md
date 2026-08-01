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

