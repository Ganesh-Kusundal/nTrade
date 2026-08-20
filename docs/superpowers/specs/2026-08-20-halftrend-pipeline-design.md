# HalfTrend Signal Pipeline Completion — Design Spec

**Date:** 2026-08-20
**Author:** nTrade Architecture Review
**Status:** Approved

## 1. Problem Statement

The HalfTrend adoption is incomplete. The indicator math is sound, but the signal pipeline is broken — `HalfTrendStrategy.on_candle_closed` is `pass`, `_bind()` is never called, and paper/live trading generates zero fills. Additionally, `latest_signal()` has an inverted NaN check, ChartPanel has dead cloud/label series, and no warmup gate prevents spurious signals.

## 2. System Context

```
Frontend (ChartPanel) ← server payload ← OverlayPipeline ← halftrend()
Paper/Live trading    ← kernel events  ← HalfTrendStrategy ← halftrend() ← _bind()
```

**Zero-parity rule:** All three paths must use the same `halftrend()` domain function. The FE never computes math.

## 3. Issues & Fixes

### 3.1 HalfTrendStrategy.on_candle_closed (Critical)

**Issue:** `on_candle_closed` is `pass`. No signals ever emitted. Paper trading runs forever without fills.

**Fix:** Implement the hook:
- Accumulate closed candles in `self._buf` (list of OHLCV dicts)
- On each new bar, build a DataFrame and call `self._bind(df)` to populate `_ht`, `_trend`, `_buy`, `_sell`
- Call `self.latest_signal(event.close)` — if a signal, `emit_signal()` with side, price, and metadata (ht, sl, tp from ATR bands)

**Why full recompute:** Reuses `halftrend()` verbatim. O(n) per bar but n≤240 in practice. Maintains zero-parity with chart overlay — the strategy always computes what the chart shows.

### 3.2 _bind() never called (Critical)

**Issue:** `_bind()` is defined but never invoked. `latest_signal()` always returns None.

**Fix:** Wire it in `on_candle_closed` (part of 3.1).

### 3.3 latest_signal() NaN check inverted (High)

**Issue:** `if self._ht[idx] != self._ht[idx]` is True only for NaN — returns NaN when ht is NaN, None when valid. Backwards.

**Fix:** Change `!=` to `==` on line 75/78.

### 3.4 ChartPanel cloud/label refs never assigned (High)

**Issue:** `cloudRef`, `buyLabelRef`, `sellLabelRef` declared but never assigned. Series created on chart but never populated.

**CRITICAL FINDING:** lightweight-charts v4.2.3 (the installed version) does NOT export `addBaselineSeries` or `addTextSeries`. The ChartPanel diff code calling these methods would throw `TypeError: chart.addBaselineSeries is not a function` at runtime. The code has never run.

**Fix:** Delete the dead series entirely — remove the `addBaselineSeries` and `addTextSeries` calls, the three refs, and all related constants (`HALFTREND_UP_BAND`, `HALFTREND_DOWN_BAND`, `HALFTREND_CLOUD_UP`, `HALFTREND_CLOUD_DOWN`). Keep only the working `htUp`/`htDown`/`atrHigh`/`atrLow` line series.

**Why delete, not upgrade:** Upgrading to lightweight-charts v5 (which has these series types) is a larger migration with breaking API changes. The cloud/label visuals are a nice-to-have that shouldn't block the signal pipeline fix.

### 3.5 Spurious signals during ATR warmup (High)

**Issue:** `trend` can flip during bars 0–99 (before ATR(100) warms up), producing buy/sell markers on uninitialized state.

**Fix:** Gate `_build_halftrend` markers to only emit after `atr_period` bars. In `overlay_pipeline.py`, change the loop start from `range(1, len(out))` to `range(atr_period, len(out))`.

### 3.6 Missing tests (Medium)

**Issue:** No warmup-golden test, no Pine reference test, no paper fill integration test.

**Fix:**
- Add `test_no_markers_during_warmup` in `test_halftrend.py`: verify no markers before bar 100
- Add `test_paper_trader_generates_fills` in `test_paper_trader_service.py`: feed 120+ bars with a reversal, assert `n_trades >= 1`
- Optional: Pine golden-value test (requires a known input/output fixture from TradingView)

## 4. Files Changed

| File | Change |
|------|--------|
| `ntrade/engines/strategies.py` | Implement `on_candle_closed`, fix NaN check |
| `ui/src/components/ChartPanel.tsx` | Delete dead `cloudRef`, `buyLabelRef`, `sellLabelRef` (LC v4 doesn't export those series) |
| `ntrade/analytics/overlay_pipeline.py` | Gate markers post-warmup |
| `tests/test_halftrend.py` | Warmup gate test |
| `tests/test_paper_trader_service.py` | Paper fill integration test |

## 5. Execution Flow (after fix)

1. Candle closes → `CandleClosedEvent` → `StrategyEngine._dispatch("on_candle_closed")`
2. `HalfTrendStrategy.on_candle_closed(event)`:
   - Accumulate candle in `self._buf`
   - Build DataFrame → `self._bind(df)` → `_ht`, `_trend`, `_buy`, `_sell` populated
   - `latest_signal(event.close)` → if buy/sell, `emit_signal()`
3. `SignalGeneratedEvent` → `RiskEngine` → `ExecutionRouter` → fill
4. Paper: fill recorded in `_fills`, surfaces via status endpoint
5. Live: order routed to broker

## 6. Invariants (after fix)

| Invariant | Enforcement |
|-----------|-------------|
| Strategy emits signals on trend flips | `on_candle_closed` → `latest_signal` → `emit_signal` |
| Signal price = bar close | `emit_signal(reference_price=event.close)` |
| No signals during warmup | `overlay_pipeline.py` gates to `range(atr_period, ...)` |
| `latest_signal()` returns None for NaN ht | Fixed NaN check |
| ChartPanel renders HalfTrend | `htUp`/`htDown`/`atrHigh`/`atrLow` line series (v4-supported) |
| Paper trading generates fills | Integration test asserts `n_trades >= 1` |
