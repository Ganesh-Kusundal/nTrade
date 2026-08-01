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

