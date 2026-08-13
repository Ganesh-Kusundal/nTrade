# TypeScript Strategy Mirror Review — `valentini.ts` vs `ValentiniScalper`

**Date:** 2026-08-12 · **Scope:** read-only review of the TS mirror (`ui/src/lib/valentini.ts`, `indicators.ts`, `rangeBars.ts`, `istTime.ts`, `marketHours.ts`) against the Python source of truth (`ntrade/engines/strategies.py` `ValentiniScalper`, `ntrade/domain/analytics/*.py`). The design doc `docs/superpowers/specs/2026-08-11-valentini-ts-mirror-resync-design.md` states **"the Python code is the spec"** — so any behavioural divergence below is a parity gap, not an acceptable difference.

Verified numerically during this review: `atrSeries` matches pandas `ewm(adjust=True)` to ~1e-15 (commit `fdb1174` claim is correct); the SELL `rr_fb` formula and the CVD window off-by-one were confirmed by execution.

---

## 1. Parity gaps (Python vs TS)

All six recently-ported features **are present structurally** (correct ordering, correct names, correct defaults), but four of the six contain real divergences. Overall verdict: **the mirror is still not behaviour-identical to the engine** — most critically because `step`/`rangeSize` are frozen at the end-of-window ATR instead of evolving per bar, and because the TS uses strict per-session state while Python's `max_window` frame leaks across day boundaries.

### CRITICAL

**C1. Static `step` / static auto `rangeSize` — frozen at the FINAL bar's ATR (lookahead), not per-bar.**
- TS: `ui/src/lib/valentini.ts:168-183` computes `rangeSize = calcAutoRange(candles, …)` and `step = max(rangeSize, atrSeries(candles).last)` **once, over the entire input array**, before the bar loop. Every step-scaled decision (SL distance, value-edge `2·step`, accumulation window `2·step`, `leg_impulse_mult·step`, `reverse_extension_mult·step`, profile bucket width) then uses that single constant for all bars — including bars that occurred *before* the ATR was measured.
- Python: `ntrade/engines/strategies.py:428-438` recomputes `calc_auto_range(frame)` and `atr(frame)` **every candle**, so `self._step` evolves and always reflects only data up to the current bar.
- **Impact:** (a) **Lookahead** — in auto mode (the UI default: `rangeSize` is only passed for `interval === 'Range'`, `TradeScreen.tsx:238-240`), the mirror's range size / step is the ATR of the *last* bar of the replay window, used for the *first* day. (b) The range-bar series (`buildRangeBars(dayBars, rangeSize, …)`, `valentini.ts:389`) is built with a frozen size, so the swing structure that feeds the direction gate, divergence exit, structure-break exit and swing-pivot trail (`valentini.ts:279-306`) diverges from Python's per-bar range bars (`strategies.py:456-458`). (c) Even with an explicit `rangeSize`, the `max(rangeSize, finalATR)` floor applies the final window ATR to early calm bars, whereas Python floors each bar by its own ATR.
- Fix direction: compute `atrSeries` incrementally (a per-bar `step[]` array, `step[i] = max(rangeSize, atr[i])`), or at minimum a rolling ATR up to bar `i` — matching Python's recompute-every-candle.

**C2. TS SELL `rr_fb` arithmetic is wrong — reported R:R is garbage and the `minRr` gate is defeated for SELLs.**
- TS: `ui/src/lib/valentini.ts:526` — `(sl > entry ? (entry - ((sl - entry) * tpMultiplier)) / (sl - entry) : 0)`. Verified: entry=120, sl=124, tp=2.0 ⇒ **28.0** (should be 2.0). This is `TP/(sl−entry)`, not `(entry−TP)/(sl−entry)`. The BUY branch (line 525) is correct.
- Python: `ntrade/engines/strategies.py:663` — `(entry - (entry - (sl - entry) * self.tp_multiplier)) / (sl - entry)` ⇒ exactly `tp_multiplier` when `sl > entry`.
- **Impact:** the reported `rr` on every SELL continuation entry is wrong (e.g. 28.0 vs 2.0); and the `rr >= minRr` gate (`valentini.ts:535`) is *always* true for SELLs, so a SELL fires even when Python would skip it (`strategies.py:669-671`, e.g. `min_rr=2.5, tp_multiplier=2.0`). The UI does not currently display `rr`, but the parity is broken and the field is a landmine.

### IMPORTANT

**I1. Per-session VWAP (TS) vs window-cumulative VWAP (Python) — different "above/below VWAP" on multi-day data.**
- TS: `sessionVwapBands` (`valentini.ts:122-150`) resets `cumPV/cumVol/cumVar` at every IST day; `sessionVwap = vwap[i]?.vwap` (`valentini.ts:388`) is therefore a fresh session VWAP.
- Python: `self._vwap = float(vwap(frame).iloc[-1])` (`strategies.py:471`) and `vwap_bands(frame, …)` (`strategies.py:472-476`) are **cumulative over the whole `max_window` frame with no session reset** — there is no day-boundary VWAP reset in `ValentiniScalper` (contrast the explicit resets in `GainzCloneStrategy`/`VwapReclaimStrategy`).
- **Impact:** on any replay/live window spanning >1 IST day, the direction gate (`close vs vwap`, `valentini.ts:271-273` vs `strategies.py:292-293`) and the `fadeExtended` ±2σ band test compare against different levels. The mirror's VWAP is the more correct intraday definition; the point is they disagree with the engine that executes fills. Same root cause as I2.

**I2. Cross-day bleed: Python's `max_window` frame contaminates the new session; TS is strictly per-day.**
- **Profile/POC/VAH/VAL:** Python builds the leg-anchored profile from `frame.iloc[self._leg_start_idx:]` (`strategies.py:484-491`) where `frame` retains up to 600 rows across midnight; on a fresh day the profile is built over "yesterday's tail + today" until the window rolls. TS builds only over `dayBars` (today only) (`valentini.ts:384`). With ~375 NSE 1m bars/session, the window never rolls within a session, so **every** session's profile in Python is contaminated with ~225 prior-day bars in continuous live/replay. Divergent SL (`val - step`), value-edge arming, `inBalance`, accumulation proximity, and reversal target.
- **Leg anchor + volume vote:** same frame vs `dayBars` divergence — `strategies.py:459-470` and `_volume_supports` `strategies.py:274-277` vs `valentini.ts:375-383` and `volumeSupports` `valentini.ts:263-267`.
- **Absorption recency:** Python `_update_phase`/`_maybe_reverse` take `bar_index >= window_len - abs_lookback` with no day guard (`strategies.py:365-366`, `532-533`); TS filters by same-IST-day (`valentini.ts:315-320`, `461-466`). TS is deliberately stricter (tests `valentini.test.ts:232-241`), but it is a divergence from the spec.

**I3. Python carries `_phase` and `_last_absorption` across the day boundary; TS force-resets them.**
- Python's session-key block (`strategies.py:442-454`) resets `_profile`, `_leg_start_idx`, `_day_pnl` but **never resets `_phase` or `_last_absorption`**. With a stale absorption in the trailing window, a day-2 bar can continue/complete an accumulation set up on day 1 (including firing a day-2 signal off a day-1 absorption).
- TS resets `phase = 'waiting'` and `lastAbsorption = null` at every boundary (`valentini.ts:358-360`) and additionally force-closes an open trade at the boundary (`valentini.ts:344-354`). The force-close is tested (`valentini.test.ts:212-230`) and has no Python equivalent — Python only closes when an *out-of-session* bar arrives (`strategies.py:734-737`). If the data jumps 15:25 → next-day 09:15 with no gap bar, Python keeps the position; TS closes it. TS is the safer behaviour, but both are spec deviations.

### MINOR

**M1. CVD window off-by-one.**
- Python: `cvd.tail(self.cvd_confirm_bars).diff().sum()` (`strategies.py:502`) — verified: `tail(n).diff().sum()` sums the last **n−1** per-bar deltas (default 2 bars).
- TS: sums `cvdConfirm` bars `[idx−n+1, idx]` (`valentini.ts:250-257`) — default 3 bars.
- Either the Python intent is "n bars" (off-by-one in Python) or "n−1 bars" (off-by-one in TS); they must agree. Borderline CVD signs can flip the aggression gate.

**M2. `minRr`-gate parity for SELL depends on C2 being fixed; default-config trade parity** — with defaults (`tp_multiplier=2.0 ≥ min_rr=1.5`) both pass, so C2 currently only breaks the *reported* `rr` value and non-default `min_rr > tp_multiplier` configs.

**M3. First-session `_day_pnl` reset differs.** Python keeps a white-box `_day_pnl` on the *first* session (only zeroes it on subsequent rollovers, `strategies.py:450-454`); TS resets `dayPnl = initialDayPnl ?? 0` at **every** boundary (`valentini.ts:366`). Production (no seeding) is identical; multi-day white-box reversal tests diverge.

**M4. Absorption `price` fallback missing.** Python uses `price=close or float(ranges.iloc[i])` (`order_flow.py:141`); TS uses `price: c.close` (`indicators.ts:214`). Only bites when `close == 0`.

**M5. Value-area price rounding: half-even vs half-up.** Python `round(x, 4)` (banker's, `volume_profile.py:131-142`) vs TS `Math.round(x*10000)/10000` (`indicators.ts:140-146`). Can shift a bucket centre / POC / VAH / VAL by 1e-4 in rare exact-`.xxxx5` cases.

**M6. Parameter clamping absent in TS.** Python clamps `leg_impulse_mult ≥ 1.0`, `accum_volume_mult ≥ 0`, `trail_arm_mult ≥ 0`, `divergence_volume_mult ≥ 0`, `reverse_extension_mult ≥ 1.0`, `cvd_confirm_bars ≥ 1` (`strategies.py:164-170`); TS passes options through unclamped (`valentini.ts:62-101`). Only diverges on hostile configs.

**M7. Session defaults are NSE-only in TS.** `valentini.ts:163-164` hard-codes 09:15–15:25; Python derives from the exchange (`strategies.py:147-159`, MCX → 09:00–23:20). The UI path compensates via `strategySession()` (`marketHours.ts:24-32`), so real usage is fine; the standalone mirror default is wrong for MCX.

**M8. Degenerate (zero-volume) profile SL.** Python would produce `sl = val - step = -step` when a profile exists but is empty (`strategies.py:654`); TS guards with `sessionVah > sessionVal` (`valentini.ts:522`) and falls back to the absorption price. Only reachable on all-zero-volume days.

### Verified in parity (no action)
- Direction-gate composition and fallback (structure null → VWAP side): `valentini.ts:269-278` vs `strategies.py:283-299` ✓
- Leg-anchored profile selection (leg slice vs whole frame): `valentini.ts:384` vs `strategies.py:484-491` ✓ (modulo I2 contamination)
- Volume-confirmed accumulation (median, 2-bar recent, 1.5×): `valentini.ts:483-489` vs `strategies.py:550-562` ✓
- Auction-exit ordering (session-close first → stop → target → divergence → structure-break → trail): `valentini.ts:395-441` vs `strategies.py:723-776` ✓
- Divergence / structure-break / swing-pivot predicates: `valentini.ts:279-306` vs `strategies.py:301-347` ✓
- PnL-gated reversal (overextension + extreme absorption + response, SL at extreme, TP = leg POC): `valentini.ts:308-333` vs `strategies.py:349-408` ✓
- `rr` gating for BUY and for prior-POC target selection: `valentini.ts:524-534` vs `strategies.py:655-668` ✓
- ATR formula: TS `atrSeries` ≡ pandas `ewm(adjust=True)` (verified to 1e-15) ✓ — but see C1 for *when* it is evaluated.

---

## 2. Analytics formula divergences

### `indicators.ts` ↔ `indicators.py` (VWAP / ATR)
| Item | TS | Python | Verdict |
|---|---|---|---|
| VWAP cum-PV/cum-Vol + volume-weighted σ | `vwapSeries`, `indicators.ts:41-70` | `vwap`, `vwap_bands`, `indicators.py:48-75` | Formula identical **per session**; **Important** — TS resets per IST day, Python's strategy never resets (see I1). The TS docstring ("like the backend's `vwap_bands`, reset at each IST trading day") is **inaccurate** — the backend has no reset. |
| ATR (Wilder ewm) | `atrSeries`, `rangeBars.ts:22-51` | `atr`, `indicators.py:29-35` | **Parity** (verified numerically). |
| TR first bar / NaN seeding | same | same | Parity. |

### `indicators.ts buildVolumeProfile` ↔ `volume_profile.py`
- Bucketing (`mid = (h+l)/2`, centred buckets `floor(price/width+0.5)`), POC tie-break (first max), value-area greedy expansion to 68% (left-pref on ties): **parity** — `indicators.ts:97-149` vs `volume_profile.py:52-144`.
- **Minor** — price rounding half-up vs half-even (M5).
- **Info** — the chart overlay calls `buildVolumeProfile(slice)` with **no step** (`ChartPanel.tsx:400`), so its POC/VAH/VAL lines use the auto `span/50` width, not the strategy's ATR-floored step. The visible "VAH/VAL" overlay does **not** show the levels the strategy trades against.

### `indicators.ts detectAbsorptions` ↔ `order_flow.py detect_absorptions`
- Rolling mean excluding current bar (partial-window handled identically), zero/NaN fallback to global mean, `vol < mult·avg` skip, `range ≤ threshold·range_size` (or self-scaling rolling range), strict comparisons, side `close>=open`, strength clip 0..1, window 20: **parity** — `indicators.ts:179-221` vs `order_flow.py:93-144`.
- **Minor** — `price` fallback `close or range` missing (M4).

### `rangeBars.ts` ↔ `range_bars.py`
- Canonical path `[O,H,L,C]`/`[O,L,H,C]`, proportional volume per segment, trailing partial `isComplete:false`, only the trailing partial incomplete, `swing_bias` on the last two *complete* bars: **parity** — `rangeBars.ts:90-152`, `160-168` vs `range_bars.py:57-116`, `119-138`.
- **Important/Critical** — the *size* fed in diverges (static final ATR vs per-bar ATR, C1), so the produced bar series diverges in auto mode.
- **Info** — TS nudges duplicate timestamps +1s (`rangeBars.ts:146-150`) purely for the chart painter; Python does not; no logic impact.

---

## 3. TS code smells

- **Duplicated VWAP implementation.** `sessionVwapBands` (`valentini.ts:122-150`) re-implements `vwapSeries` (`indicators.ts:41-70`) with a different return shape (null-gap array vs three series). Same math, two homes.
- **`istMinuteOfDay` (`valentini.ts:109-112`) re-derives the IST offset** already centralized as `IST_OFFSET_S` in `istTime.ts:16` — two places to keep the +5:30 constant honest.
- **Tautological tests (false confidence in the ATR floor and direction gate):**
  - `valentini.test.ts:305-313` "floors the step at 1x ATR" asserts `res.trades.length >= 0` — always true, cannot fail.
  - `valentini.test.ts:315-326` direction-gate test asserts `buys(res) >= 0` — always true.
  The real gates are only exercised indirectly (structure-break/divergence via `injectedRangeBars`), never the ATR floor or the direction veto.
- **`ValentiniTrade.rr` is written but never read by the UI** (only tests touch it) and carries the C2 garbage for SELLs — a wrong value waiting to be displayed.
- **`ValentiniResult.phase` / `.lastAbsorption` are dead output** — `TradeScreen.tsx:362` passes the whole result to `ChartPanel`, which only reads `.trades`; phase/lastAbsorption are consumed only by tests.
- **Two ways to compute the range size**: `rangeSizeFromTicks` (`rangeBars.ts:59-62`, used in `useCandles.ts:85`) vs inline `rangeTicks * contract.tick_size` in `TradeScreen.tsx:238-239`.
- **`Absorption.strength` / `.volume`** produced by `detectAbsorptions` are unused by both the strategy and the chart (only `barIndex/side/price`).
- **One-line `maybeReverse` guards**: the caller already checks `dayPnl > 0` (`valentini.ts:454`) and `maybeReverse` re-checks `dayPnl <= 0` (`valentini.ts:309`) — harmless redundancy.
- **`marketHours.ts` duplicates the exchange→session mapping** that Python has in `ntrade/domain/market_hours.py` — a third copy of the schedule alongside `strategies.py:147-159` (see §4).

---

## 4. Architecture verdict on the two-implementation problem

**Verdict: the mirror has demonstrably failed to stay in lockstep, and it will keep failing.** This is the third drift cycle (design doc + plan + six port commits for what the task list calls "six recently-ported features"), and this review still finds two critical and three important divergences. The root cause is structural: *two independent implementations of a stateful strategy, hand-synchronized by reading one another.* Any behavioural nuance the Python strategy gains — window trimming, per-bar ATR, cross-day carry, position sizing, pending-fill handling — has to be rediscovered and re-ported by hand in a different language, and the chart can silently show trades the engine would never take (or vice-versa) with no automated cross-check. The TDD-style test suites on *each* side only prove self-consistency, never parity.

### De-duplication options (with tradeoffs)

**Option A — Serve signals from the Python API (recommended).**
The engine already emits structured signals with full metadata (`emit_signal(… phase, sl, tp, rr, target, exit_reason, …)` — `strategies.py:684-693`, `787-792`). The API layer (`api/`) already streams candles to the UI. Expose a `/signals` (or attach trades to the existing candle stream) endpoint; the UI consumes engine-produced `{side, entryIndex, exitIndex, sl, tp, reason}` instead of running `runValentini`.
- **Pros:** single source of truth; every trade drawn is a trade the engine actually considered, *including* live fills and rejected-entry paths the mirror cannot model; kills the whole mirror (valentini.ts + the duplicated analytics) — a net deletion of ~900 lines and two bug classes.
- **Cons:** needs a round-trip; offline/air-gapped replay loses the instant local recompute; replay scrubbing would need the server to recompute per cursor (or the UI to keep the last-computed signal list keyed by bar count). Latency is 1 small JSON per completed bar — negligible.
- **Best fit:** this repo already has a working API + replay stream; this is the least code and the highest fidelity.

**Option B — Keep the mirror, but drive both sides from a shared, table-driven spec + conformance tests.**
Extract every threshold and ordering rule (all 22 option defaults, the exit-precedence order, the day-carry semantics, the window/CVD definitions) into one JSON/YAML "strategy spec"; both implementations read it; a CI job runs the **same fixture** through `ValentiniScalper` (via a replay harness) and `runValentini` and diffs the trade lists (side/entry/exit/reason/SL/TP) — the missing cross-check today.
- **Pros:** keeps offline replay; catches drift in CI instead of reviews; small change.
- **Cons:** still two implementations of the *logic* (the spec only fixes constants/ordering, not the subtle computation divergences C1/I1/I2 found here); requires writing the conformance harness, which is real work; the hard bugs (static step, cross-day bleed) live in the computation, not the constants, so the spec wouldn't catch them without also porting the recompute-every-candle semantics.
- **Best fit:** interim hardening *while* Option A is built.

**Option C — Generated mirror (codegen from Python).**
Port the Python into a restricted spec DSL and generate the TS. High up-front cost, brittle across language idioms (pandas vs arrays, `float(NA)` semantics, ewm adjust), and the generated TS still has to be reviewed. Only pays off if the strategy churns frequently and the mirror must stay client-side. **Not recommended** for a visualization-only overlay.

### Recommendation
**Adopt Option A as the destination (server-side signals), with Option B's conformance harness as an immediate stopgap** — and while the mirror lives, fix C1 (per-bar step/ATR) and C2 (SELL `rr_fb`) first, since they are provable arithmetic/lookahead bugs that the UI could surface tomorrow. The de-dup is not optional eventually: three months from now the "six features" will be twelve, and the re-sync cost is already non-linear.

### Immediate fix list (if the mirror is kept)
1. `valentini.ts:526` — correct SELL `rr_fb` (Critical, one-line).
2. `valentini.ts:168-183` + `389` — per-bar ATR/step and per-bar range size (Critical; also removes the lookahead).
3. `valentini.ts:250-257` vs `strategies.py:502` — pick one CVD window and match it (Minor→Important).
4. Decide the day-carry semantics explicitly (I2/I3): either make Python reset `_phase`/`_last_absorption`/VWAP and drop the `max_window` bleed, or accept and *document* the TS divergence — the current state is an undocumented split.
5. Replace the tautological tests (`valentini.test.ts:305-326`) with assertions that can fail.
