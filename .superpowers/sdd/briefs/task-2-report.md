# Task Group 2 Report — B-007 / F-005: backtest candles carry real OHLCV

## Status: DONE

## What changed

### `ntrade/engines/candle_engine.py`
- `CandleEngine.__init__` now also subscribes `context.bus.subscribe(QuoteEvent, self.on_quote)` (alongside the existing `TickEvent` subscription).
- New `on_quote(self, event)`:
  - Returns immediately unless `self.ctx.mode == "backtest"` — live/replay `QuoteEvent`s carry day-session OHLCV (dhan_feed.py, market_feed.py) and must not mint bar candles.
  - Otherwise ingests the bar **authoritatively** via new `_ingest_bar(...)`: sets `open/high/low/close/volume` directly from the event fields (`close` maps to `QuoteEvent.ltp`, which the backtest producer sets to the bar close) instead of min/max accumulation.
  - Records `self._bar_seeded[symbol] = bucket` so the paired close tick is skipped.
- `on_tick`: now skips ingestion when `self._bar_seeded.get(event.symbol) == self._bucket(event.ts)` — the simulator's close tick is the same bar's print, and ingesting it would double-count volume. The seed naturally advances/overwrites as the bucket advances.
- `_bar_seeded: dict[str, int]` is a `(symbol -> bucket)` last-seed key (the brief's second option), so no explicit cleanup is needed beyond overwrite-on-advance.
- Tick-only ingestion path (`_ingest`) is unchanged; existing replay-mode candle tests are untouched by the mode gate.

### `tests/test_candle_engine.py` (new, 3 tests)
1. `test_backtest_candles_carry_real_ohlcv` — runs `BacktestSimulator(timeframe="5m", initial_cash=100_000.0, statutory=None)` over a 20-bar `_ohlcv` frame (per-bar `open`/`close` differ by 1.0, `high`/`low` by 3.0) and asserts all 20 closed candles carry the real bar values: `open=100+i`, `high=102+i`, `low=99+i`, `close=101+i`, `volume=1000`, and `open != close` (not degenerate). Was failing before the fix (open=high=low=close).
2. `test_backtest_indicators_populated_with_real_candles` — after the run, asserts `instrument._indicators` holds `rsi_14`, `atr_14`, `stx_10_3`, and `atr_14 ≈ 3.0` (the real 3.0 high-low range; degenerate candles collapse ATR to ~1.0). Was failing before the fix.
3. `test_candle_engine_ignores_live_quote_events` — a `QuoteEvent` published in `mode="replay"` must produce zero candles (guard for the mode gate). Passes both before and after (regression guard).

## Checklist verification (against the brief)
- [x] `CandleEngine.__init__` subscribes `QuoteEvent` → `on_quote`.
- [x] `on_quote` gates on `self.ctx.mode == "backtest"`, ingests bar authoritatively, records `_bar_seeded[symbol] = bucket`.
- [x] `on_tick` skips when the tick's bucket was bar-seeded; seed advances with the bucket.
- [x] Failing tests written first (confirmed red), then impl, then green.
- [x] IndicatorEngine populates `rsi_14`/`atr_14`/`stx_10_3` after ≥10 real bars.
- [x] Tick-only tests stay green (`test_market_engine_projects_quote`, `test_candle_engine_closes_candle_on_next_bucket`, `test_candle_engine_flush_closes_partial`) — replay mode, untouched.
- [x] `test_backtest_simulator_produces_equity_curve` still passes (`BuySellOnCandles` trades on candle count + `event.close`, both unchanged).
- [x] Full suite green: `./.venv/bin/python -m pytest -q`.

## Test commands and output

TDD — failing test first:
```
$ ./.venv/bin/python -m pytest -q tests/test_candle_engine.py
FAILED tests/test_candle_engine.py::test_backtest_candles_carry_real_ohlcv
FAILED tests/test_candle_engine.py::test_backtest_indicators_populated_with_real_candles
2 failed, 1 passed in 0.33s
```

After implementation:
```
$ ./.venv/bin/python -m pytest -q tests/test_candle_engine.py
...                                                                      [100%]
3 passed in 0.30s
```

Full suite:
```
$ ./.venv/bin/python -m pytest -q
<…>
[100%]
621 passed in 5.66s
```
(618 baseline + 3 new tests; no regressions.)

## Commit
- `c215df6` — `backtest candles carry real OHLCV; skip paired close tick (B-007, F-005)` (only `ntrade/engines/candle_engine.py` + `tests/test_candle_engine.py` staged; pre-existing uncommitted dhan*.py / kanban / scratch changes left untouched).

## Concerns
- `QuoteEvent` has no `close` field; the bar close is taken from `QuoteEvent.ltp`, which the backtest producer sets to the bar close. If another backtest producer ever publishes `ltp != close`, candles would carry the wrong close. Not an issue for the current simulator (simulator.py:130 sets `ltp=close`).
- In backtest, the bar's `QuoteEvent` is authoritative for its whole bucket: any additional ticks in that bucket are skipped. The current simulator publishes exactly one quote + one tick per bar, so this is correct and avoids volume double-counting. A backtest source that publishes *multiple* trades per bar would undercount volume (by design of this fix — the bar is the unit).
- The report's assertion tolerance `atr_14 ≈ 3.0 ± 0.5` accommodates pandas ewm warm-up (first row's `prev_close` is NaN), which lands ATR at ~2.93, not exactly 3.0.
