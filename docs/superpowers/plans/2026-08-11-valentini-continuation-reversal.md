# Valentini Continuation + Reversal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `ValentiniScalper` in line with Fabio Valentini's actual AMT model (docs/amt-model-spec.md): an up-front Direction gate, an auction-following trail replacing the fixed R-multiple target, and a PnL-gated reversal model.

**Architecture:** All changes in `ntrade/engines/strategies.py` (ValentiniScalper) plus one new pure helper `swing_bias()` in `ntrade/domain/analytics/range_bars.py`. The Direction gate (`_direction`) is computed per candle before the Triple-A trigger; the trail ratchets SL under swing pivots and exits on volume divergence / structure break; reversal is a separate path gated on positive day PnL. No event/kernel/execution changes; fills stay MARKET at `reference_price`, so zero-parity holds.

**Tech Stack:** Python 3, pandas, the existing event-bus strategy kernel. Tests run with `python -m pytest`.

## Global Constraints

- Modify ONLY: `ntrade/engines/strategies.py`, `ntrade/domain/analytics/range_bars.py`, `tests/test_valentini_leg_anchor.py` (new tests). `tests/test_valentini_strategy.py` is already committed (WIP base `ca7b4d5`) — do NOT edit it; it must keep passing (24 tests).
- Existing constructor params keep defaults/semantics. New knobs (with exact defaults): `direction_volume_mult: float = 1.0`, `trail_arm_mult: float = 1.0`, `divergence_volume_mult: float = 0.6`, `reverse_extension_mult: float = 2.0`.
- Zero-parity: `tests/test_zero_parity_across_modes.py` must keep passing (`3 passed`).
- The Direction gate may change which setups fire in synthetic fixtures — tuning fixtures is expected and sanctioned, but only inside `tests/test_valentini_leg_anchor.py`.
- Keep it lazy: no new modules beyond the one helper, no refactors of unrelated code.
- Mark deliberate shortcuts with `ponytail:` comments.

---

### Task 1: `swing_bias` helper in range_bars.py

**Files:**
- Modify: `ntrade/domain/analytics/range_bars.py` (append function)
- Test: `tests/test_valentini_leg_anchor.py` (new test)

**Interfaces:**
- Produces: `swing_bias(bars: pd.DataFrame) -> str | None` — `"BUY"` (higher high AND higher low), `"SELL"` (lower high AND lower low), else `None`. Used by Task 2's `_direction()`.

- [ ] **Step 1: Add shared test helpers + write the failing test**

`tests/test_valentini_leg_anchor.py` currently lacks `import pandas as pd` and
the `_fills` / `_signals` helpers (they live in `test_valentini_strategy.py`,
which this file deliberately does not import). Add `import pandas as pd` to the
imports, and append the helpers + the failing tests:

```python
import pandas as pd
```

(place in the import block with the other imports). Then append:

```python
def _fills(k):
    from ntrade.events.order import OrderFilledEvent
    return [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]


def _signals(k):
    from ntrade.events.risk import SignalGeneratedEvent
    return [e for e in k.bus.history if isinstance(e, SignalGeneratedEvent)]


# ------------------------------------------------------------------ swing bias

def test_swing_bias_bullish_on_higher_high_and_low():
    from ntrade.domain.analytics.range_bars import swing_bias
    bars = pd.DataFrame({
        "high": [100.0, 101.0], "low": [99.0, 100.0],
        "close": [100.5, 100.8], "is_complete": [True, True],
    })
    assert swing_bias(bars) == "BUY"


def test_swing_bias_bearish_on_lower_high_and_low():
    from ntrade.domain.analytics.range_bars import swing_bias
    bars = pd.DataFrame({
        "high": [101.0, 100.0], "low": [100.0, 99.0],
        "close": [100.8, 99.8], "is_complete": [True, True],
    })
    assert swing_bias(bars) == "SELL"


def test_swing_bias_indeterminate():
    from ntrade.domain.analytics.range_bars import swing_bias
    # fewer than two completed bars -> no vote
    bars = pd.DataFrame({"high": [100.0], "low": [99.0],
                         "close": [99.5], "is_complete": [True]})
    assert swing_bias(bars) is None
    # incomplete latest bar is excluded -> only one completed -> None
    bars2 = pd.DataFrame({"high": [100.0, 102.0], "low": [99.0, 101.0],
                          "close": [99.5, 101.5],
                          "is_complete": [True, False]})
    assert swing_bias(bars2) is None
```

Note: add `import pandas as pd` at the top of `tests/test_valentini_leg_anchor.py` if not already present.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_swing_bias_bullish_on_higher_high_and_low -q`
Expected: FAIL — `ImportError: cannot import name 'swing_bias'`.

- [ ] **Step 3: Implement `swing_bias`**

Append to `ntrade/domain/analytics/range_bars.py`:

```python
def swing_bias(bars: pd.DataFrame) -> str | None:
    """Directional vote from the last two COMPLETED range bars.

    Bullish when the latest completed bar makes a higher high AND a higher low
    than the prior; bearish on a lower high + lower low; ``None`` otherwise
    (fewer than two completed bars, or an inside/outside bar — no vote).
    """
    if bars is None or bars.empty or "is_complete" not in bars:
        return None
    done = bars[bars["is_complete"].astype(bool)]
    if len(done) < 2:
        return None
    p, c = done.iloc[-2], done.iloc[-1]
    ph, pl = float(p["high"]), float(p["low"])
    ch, cl = float(c["high"]), float(c["low"])
    if ch > ph and cl > pl:
        return "BUY"
    if ch < ph and cl < pl:
        return "SELL"
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_swing_bias_bullish_on_higher_high_and_low tests/test_valentini_leg_anchor.py::test_swing_bias_bearish_on_lower_high_and_low tests/test_valentini_leg_anchor.py::test_swing_bias_indeterminate -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ntrade/domain/analytics/range_bars.py tests/test_valentini_leg_anchor.py
git commit -m "feat: range-bar swing_bias directional vote"
```

---

### Task 2: Direction gate (`_direction`)

**Files:**
- Modify: `ntrade/engines/strategies.py` (import, constructor, new methods)
- Test: `tests/test_valentini_leg_anchor.py`

**Interfaces:**
- Consumes: `swing_bias` (Task 1), `self._range_bars`, `self._vwap`, `self._leg_start_idx`, `self._step`, `self.direction_volume_mult`.
- Produces: `self._direction(close: float) -> str | None` — `"BUY" | "SELL" | None`. `self._volume_supports() -> bool`. Used by Task 3 (trigger wiring) and Task 4 (unchanged).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_valentini_leg_anchor.py`:

```python
# ------------------------------------------------------------------ direction gate

def test_direction_bullish_when_structure_volume_vwap_agree(monkeypatch):
    from ntrade.domain.analytics.range_bars import swing_bias
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: "BUY")
    _uptrend_bars(k, 30)
    strat._vwap = 110.0
    assert strat._direction(close=122.0) == "BUY"


def test_direction_none_when_volume_does_not_support():
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             direction_volume_mult=100.0)
    k.register_strategy(strat)
    _uptrend_bars(k, 30, volume=100)          # impulse leg vol ~3000
    strat._vwap = 110.0
    assert strat._direction(close=122.0) is None


def test_direction_none_when_structure_vwap_disagree(monkeypatch):
    from ntrade.domain.analytics.range_bars import swing_bias
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: "SELL")
    _uptrend_bars(k, 30)
    strat._vwap = 110.0
    # structure says SELL, VWAP says BUY -> no trade
    assert strat._direction(close=122.0) is None


def test_direction_falls_back_to_vwap_without_structure_vote(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: None)
    _uptrend_bars(k, 30)
    strat._vwap = 110.0
    assert strat._direction(close=122.0) == "BUY"
    assert strat._direction(close=105.0) == "SELL"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_direction_bullish_when_structure_volume_vwap_agree -q`
Expected: FAIL — `AttributeError: 'ValentiniScalper' object has no attribute '_direction'`.

- [ ] **Step 3: Import `swing_bias`**

`strategies.py:18` currently imports `from ntrade.domain.analytics.range_bars import calc_auto_range, build_range_bars`. Change to:

```python
from ntrade.domain.analytics.range_bars import (
    build_range_bars, calc_auto_range, swing_bias)
```

- [ ] **Step 4: Add the constructor knobs**

In `ValentiniScalper.__init__`, after `accum_volume_mult: float = 1.5` in the signature add:

```python
                 direction_volume_mult: float = 1.0,
                 trail_arm_mult: float = 1.0,
                 divergence_volume_mult: float = 0.6,
                 reverse_extension_mult: float = 2.0):
```

After `self.accum_volume_mult = max(0.0, float(accum_volume_mult))` add:

```python
        self.direction_volume_mult = max(0.0, float(direction_volume_mult))
        self.trail_arm_mult = max(0.0, float(trail_arm_mult))
        self.divergence_volume_mult = max(0.0, float(divergence_volume_mult))
        self.reverse_extension_mult = max(1.0, float(reverse_extension_mult))
```

- [ ] **Step 5: Add `_volume_supports` and `_direction`**

Insert after the `_cvd_agrees` method (before `on_candle_closed`):

```python
    def _volume_supports(self) -> bool:
        """Direction-gate volume vote: the current impulse leg's volume must
        clear direction_volume_mult x the prior median per-bar volume."""
        frame = pd.DataFrame(self._rows)
        if frame.empty or "volume" not in frame:
            return False
        leg = frame.iloc[self._leg_start_idx:]
        if leg.empty:
            return False
        prior = frame.iloc[:self._leg_start_idx]["volume"].astype(float)
        base = float(prior.median()) if len(prior) else 0.0
        if base <= 0:
            return True
        return float(leg["volume"].sum()) >= self.direction_volume_mult * base

    def _direction(self, close: float) -> str | None:
        """Gate 0: who controls the auction.

        Three votes must agree (structure + volume + VWAP). Structure has no
        vote when < 2 completed range bars exist -> the VWAP side alone
        decides (volume still required). All-else-None = no trade.
        """
        if not self._volume_supports():
            return None
        vwap_side = ("BUY" if close > self._vwap
                     else ("SELL" if close < self._vwap else None))
        if vwap_side is None:
            return None
        struct = swing_bias(self._range_bars)
        if struct is None:
            return vwap_side
        return vwap_side if struct == vwap_side else None
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: PASS — 8 passed (3 swing_bias + 1 existing ATR + 1 existing leg-anchor + 1 existing accumulation + ... count will show in output; at minimum all green).

- [ ] **Step 7: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: valentini direction gate (structure + volume + vwap)"
```

---

### Task 3: Wire Direction gate into the continuation trigger

**Files:**
- Modify: `ntrade/engines/strategies.py` (`_update_phase`)
- Test: `tests/test_valentini_leg_anchor.py`

**Interfaces:**
- Consumes: `self._direction(close)` (Task 2).
- Produces: nothing new — the Triple-A trigger now also requires `side == self._direction(close)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_valentini_leg_anchor.py`:

```python
# ------------------------------------------------------------------ direction-gated trigger

def test_trigger_requires_absorption_side_matches_direction(monkeypatch):
    from ntrade.domain.analytics.range_bars import swing_bias
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    # Direction is SELL (structure) but the absorption is BUY at VAL -> the
    # continuation trigger must NOT fire even though every other gate passes.
    monkeypatch.setattr("ntrade.engines.strategies.swing_bias",
                        lambda bars: "SELL")
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)          # BUY absorption at VAL
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)               # above VWAP, not in balance
    assert not any(f.side == "BUY" for f in _fills(k))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_trigger_requires_absorption_side_matches_direction -q`
Expected: FAIL — a BUY fill appears (today's trigger only checks absorption side + VWAP).

- [ ] **Step 3: Gate the trigger on `_direction`**

In `_update_phase`, the accumulating block (currently `if side == "BUY" and close > self._vwap:` at `strategies.py:420`) — replace the two branch conditions:

```python
            dirn = self._direction(close)
            if side == "BUY" and dirn == "BUY":
                if self.fade_extended and self._extended(close, side):
                    return  # extended beyond the band — wait for the pullback
                if not self._cvd_agrees(side):
                    return  # OHLCV CVD proxy disagrees — wait
                if self._depth_blocked(side, event.symbol):
                    return  # no live buy-side depth pressure — wait
                self._phase = "signal"
            elif side == "SELL" and dirn == "SELL":
                if self.fade_extended and self._extended(close, side):
                    return
                if not self._cvd_agrees(side):
                    return
                if self._depth_blocked(side, event.symbol):
                    return
                self._phase = "signal"
```

(Keep `_in_balance` check that precedes this block unchanged.)

- [ ] **Step 4: Run the new test + full leg-anchor + existing strategy suite**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: all pass (9 tests).

Run: `python -m pytest tests/test_valentini_strategy.py -q`
Expected: `24 passed`. If any fixture now fails because the direction gate changed which setups fire, adjust the *fixture data* in `tests/test_valentini_leg_anchor.py`-style tests only — do NOT edit `test_valentini_strategy.py`. If a `test_valentini_strategy.py` test fails, report it (BLOCKED) rather than editing that file.

- [ ] **Step 5: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: gate valentini trigger on direction"
```

---

### Task 4: Auction-following trail (swing-pivot + volume-divergence)

**Files:**
- Modify: `ntrade/engines/strategies.py` (`_emit_entry`, `_manage_exit`, `on_order_filled`)
- Test: `tests/test_valentini_leg_anchor.py`

**Interfaces:**
- Consumes: `self.trail_arm_mult`, `self.divergence_volume_mult`, `self._range_bars`, `self._leg_start_idx`, `self._step`.
- Produces: `self._last_swing_low() -> float | None`, `self._divergence_exit(act) -> bool`, `self._structure_broken(side, close) -> bool`. `_active` dict gains `impulse_volume` and may have `tp=None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_valentini_leg_anchor.py`:

```python
# ------------------------------------------------------------------ auction trail

def _runner_long(k, monkeypatch):
    """Drive the strategy to a filled BUY entry (runner: tp=None) at bar 32,
    then override _range_bars with a controlled completed-bar series so the
    trail/exit logic is tested deterministically (white-box), mirroring the
    _fixed_profile pattern."""
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # BUY entry at 122
    buys = [f for f in _fills(k) if f.side == "BUY"]
    assert buys, "entry did not fill"
    # Controlled range-bar series: latest completed bar makes a higher low
    # (trail anchor at 121) then a down-close through it (structure break).
    strat._range_bars = pd.DataFrame({
        "high": [116.0, 120.0, 124.0, 127.0],
        "low":  [114.0, 117.0, 121.0, 119.0],
        "close":[115.5, 119.0, 123.0, 118.5],
        "volume":[100.0, 100.0, 100.0, 100.0],
        "is_complete":[True, True, True, True],
    })
    strat._leg_start_idx = 0
    return strat, buys[0]


def test_runner_has_no_fixed_target(monkeypatch):
    k = _kernel()
    strat, _ = _runner_long(k, monkeypatch)
    sig = [s for s in _signals(k)
           if s.side == "BUY" and s.metadata.get("phase") == "signal"]
    assert sig
    assert sig[0].metadata.get("tp") is None       # runner, no hard target
    assert sig[0].metadata.get("target") == "runner"


def test_structure_break_exit(monkeypatch):
    k = _kernel()
    strat, _ = _runner_long(k, monkeypatch)
    # The controlled series ends with a down-close (118.5) through the prior
    # bar's low (121.0): structure break -> SELL exit on the next candle.
    _candle(k, 33, close=118.5, open_=122.0, high=122.5, low=118.0, volume=100)
    sells = [f for f in _fills(k) if f.side == "SELL"]
    assert sells, "structure break must exit the long"
    reason = [s.metadata.get("exit_reason") for s in _signals(k)
              if s.side == "SELL"]
    assert "structure_break" in reason, f"got reasons {reason}"


def test_volume_divergence_exit(monkeypatch):
    k = _kernel()
    strat, _ = _runner_long(k, monkeypatch)
    # Controlled series ends with a HIGHER high (127) on weak volume; the next
    # candle makes a new high on low volume -> divergence exit.
    strat._range_bars = pd.DataFrame({
        "high": [116.0, 120.0, 124.0, 127.0],
        "low":  [114.0, 117.0, 121.0, 122.0],
        "close":[115.5, 119.0, 123.0, 126.0],
        "volume":[100.0, 100.0, 100.0, 100.0],
        "is_complete":[True, True, True, True],
    })
    _candle(k, 33, close=128.0, open_=126.5, high=129.0, low=126.0, volume=80)
    sells = [f for f in _fills(k) if f.side == "SELL"]
    assert sells
    reason = [s.metadata.get("exit_reason") for s in _signals(k)
              if s.side == "SELL"]
    assert "divergence" in reason, f"got reasons {reason}"
```

Note: these tests are white-box — they override `_range_bars` directly so the
trail helpers (`_last_swing_low`, `_divergence_exit`, `_structure_broken`)
operate on a controlled series, exactly like `_fixed_profile` does for the
volume profile. `impulse_volume` comes from the strategy's real `_rows` at
entry (the absorption spike at bar 30 → ~4700), so the divergence test's
`volume=80 < 0.6 × 4700` holds.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_runner_has_no_fixed_target -q`
Expected: FAIL — `sig[0].metadata.get("tp")` is the R-multiple fallback, not `None`.

- [ ] **Step 3: Add swing/trail helpers**

Insert after `_direction`:

```python
    def _last_swing_low(self) -> float | None:
        """Low of the most recent completed range bar that is higher than the
        prior completed bar's low (the trail anchor for a long)."""
        done = self._range_bars[self._range_bars["is_complete"].astype(bool)]
        if len(done) < 2:
            return None
        pl = float(done.iloc[-2]["low"])
        cl = float(done.iloc[-1]["low"])
        return cl if cl > pl else None

    def _last_swing_high(self) -> float | None:
        """High of the most recent completed range bar that is lower than the
        prior completed bar's high (the trail anchor for a short)."""
        done = self._range_bars[self._range_bars["is_complete"].astype(bool)]
        if len(done) < 2:
            return None
        ph = float(done.iloc[-2]["high"])
        ch = float(done.iloc[-1]["high"])
        return ch if ch < ph else None

    def _divergence_exit(self, act) -> bool:
        """Volume-price divergence: a new swing extreme on volume <
        divergence_volume_mult x the impulse-leg volume -> exit."""
        done = self._range_bars[self._range_bars["is_complete"].astype(bool)]
        if len(done) < 2:
            return False
        p, c = done.iloc[-2], done.iloc[-1]
        imp_vol = float(act.get("impulse_volume") or 0.0)
        if imp_vol <= 0:
            return False
        weak = float(c["volume"]) < self.divergence_volume_mult * imp_vol
        if not weak:
            return False
        if act["side"] == "BUY":
            return float(c["high"]) > float(p["high"])
        return float(c["low"]) < float(p["low"])

    def _structure_broken(self, side: str, close: float) -> bool:
        """A completed range bar closes through the prior bar's structure
        extreme -> the auction narrative broke."""
        done = self._range_bars[self._range_bars["is_complete"].astype(bool)]
        if len(done) < 2:
            return False
        p = done.iloc[-2]
        if side == "BUY":
            return float(done.iloc[-1]["close"]) < float(p["low"])
        return float(done.iloc[-1]["close"]) > float(p["high"])
```

- [ ] **Step 4: Compute `impulse_volume` and drop the hard TP in `_emit_entry`**

In `_emit_entry`, after `step = self._step` (currently `strategies.py:481`), add:

```python
        leg = pd.DataFrame(self._rows).iloc[self._leg_start_idx:]
        impulse_volume = float(leg["volume"].sum()) if not leg.empty else 0.0
```

Then replace the TP computation block (`tp_fallback`/`tp, rr = ...` for both sides — the whole `if side == "BUY":` / `else:` SL+TP selection) with a version that:
- keeps the SL exactly as today (VAL−step long / VAH+step short);
- computes the prior-POC R:R exactly as today, and if `rr_poc >= self.min_rr`, sets `tp = prior_poc`, `target = "prior_poc"`, `rr = rr_poc`;
- otherwise sets `tp = None`, `target = "runner"`, and uses the R-multiple R:R only to enforce `min_rr` (skip the setup if the R-multiple R:R is below `min_rr`):

```python
        if side == "BUY":
            sl = (val - step) if val is not None else (self._last_absorption.price - step)
            rr_fb = (entry + (entry - sl) * self.tp_multiplier - entry) / (entry - sl) if entry > sl else 0.0
            tp, rr, target = None, rr_fb, "runner"
            if self._prior_poc is not None and self._prior_poc > entry:
                rr_poc = (self._prior_poc - entry) / (entry - sl) if entry > sl else 0.0
                if rr_poc >= self.min_rr:
                    tp, rr, target = self._prior_poc, rr_poc, "prior_poc"
        else:
            sl = (vah + step) if vah is not None else (self._last_absorption.price + step)
            rr_fb = (entry - (entry - (sl - entry) * self.tp_multiplier)) / (sl - entry) if sl > entry else 0.0
            tp, rr, target = None, rr_fb, "runner"
            if self._prior_poc is not None and self._prior_poc < entry:
                rr_poc = (entry - self._prior_poc) / (sl - entry) if sl > entry else 0.0
                if rr_poc >= self.min_rr:
                    tp, rr, target = self._prior_poc, rr_poc, "prior_poc"
        if rr < self.min_rr:
            logger.info("valentini skip: RR %.2f < min %.2f", rr, self.min_rr)
            return
```

Then update the pending dict (currently `strategies.py:516-517`) to carry `impulse_volume` and `target`:

```python
        self._pending = {"symbol": event.symbol, "side": side, "entry": entry,
                         "sl": sl, "tp": tp, "qty": qty,
                         "impulse_volume": impulse_volume}
```

And update the emit metadata (currently `strategies.py:525`) — replace `target="prior_poc" if tp == self._prior_poc else "r_multiple"` with `target=target`.

- [ ] **Step 5: Rewrite `_manage_exit` for the auction trail**

Replace the body of `_manage_exit` (the BUY/SELL stop/target/breakeven block, currently `strategies.py:559-579`) with:

```python
        act = self._active
        low, high, close = float(event.low), float(event.high), float(event.close)
        risk = abs(act["entry"] - act["sl"])
        side = act["side"]
        if side == "BUY":
            if low <= act["sl"]:
                self._exit(event, "SELL", act["sl"], reason="stop")
                return
            if act.get("tp") is not None and high >= act["tp"]:
                self._exit(event, "SELL", act["tp"], reason="target")
                return
            if self._divergence_exit(act):
                self._exit(event, "SELL", close, reason="divergence")
                return
            if self._structure_broken(side, close):
                self._exit(event, "SELL", close, reason="structure_break")
                return
            # Auction trail: once >= trail_arm_mult R in profit, ratchet the
            # stop under the last higher low. ponytail: swing-pivot trail;
            # a per-tick ATR trail is the upgrade path if stops get wicked.
            if high >= act["entry"] + self.trail_arm_mult * risk:
                pivot = self._last_swing_low()
                if pivot is not None and pivot > act["sl"]:
                    act["sl"] = pivot
        else:
            if high >= act["sl"]:
                self._exit(event, "BUY", act["sl"], reason="stop")
                return
            if act.get("tp") is not None and low <= act["tp"]:
                self._exit(event, "BUY", act["tp"], reason="target")
                return
            if self._divergence_exit(act):
                self._exit(event, "BUY", close, reason="divergence")
                return
            if self._structure_broken(side, close):
                self._exit(event, "BUY", close, reason="structure_break")
                return
            # Auction trail (short): once >= trail_arm_mult R in profit,
            # ratchet the stop down to just above the last lower high.
            if low <= act["entry"] - self.trail_arm_mult * risk:
                pivot = self._last_swing_high()
                if pivot is not None and pivot < act["sl"]:
                    act["sl"] = pivot
```

- [ ] **Step 6: Run the trail tests to verify they pass**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: all pass. Run: `python -m pytest tests/test_valentini_strategy.py -q` — expect `24 passed`. If `test_stop_wins_when_bar_hits_both_stop_and_target` fails (it asserts stop wins when a bar touches both), the divergence/structure-break checks must be ordered AFTER the stop check — they are (stop is first). If it still fails, report (BLOCKED).

- [ ] **Step 7: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: auction-following trail (swing-pivot + divergence)"
```

---

### Task 5: PnL-gated reversal model

**Files:**
- Modify: `ntrade/engines/strategies.py` (constructor state, `on_candle_closed` dispatch, `_exit`, new `_maybe_reverse` + `_emit_reversal`)
- Test: `tests/test_valentini_leg_anchor.py`

**Interfaces:**
- Consumes: `self._day_pnl`, `self._profile`, `self._vwap_upper/lower`, `self.reverse_extension_mult`, `self._step`, `self._absorptions`, `self.abs_lookback`.
- Produces: `self._maybe_reverse(event) -> bool` — returns True when it emitted a reversal (then `on_candle_closed` skips the continuation chain).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_valentini_leg_anchor.py`:

```python
# ------------------------------------------------------------------ reversal

def test_reversal_not_armed_without_day_profit():
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5)
    k.register_strategy(strat)
    assert strat._day_pnl == 0.0
    # Overextension + absorption even when all else aligns -> no reversal.
    for i in range(35):
        _candle(k, i, close=120.0 - i * 0.5, volume=100)
    _absorption_bar(k, 35, at=90.0)
    _candle(k, 36, close=92.0)
    assert _fills(k) == []                          # flat: reversal is PnL-gated


def test_reversal_fires_to_poc_after_profit(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5)
    k.register_strategy(strat)
    strat._day_pnl = 5000.0                      # profitable day (white-box)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    # Downtrend carries price far below the POC (deep oversold), a BUY
    # absorption (big volume, tiny range, close>=open) appears at the extreme,
    # then price responds back up -> BUY reversal targeting the leg POC 118.
    for i in range(35):
        _candle(k, i, close=120.0 - i * 0.5, volume=100)   # last close 102.5
    _absorption_bar(k, 35, at=90.0)              # BUY absorption deep below POC
    _candle(k, 36, close=92.0)                   # respond back up
    buys = [f for f in _fills(k) if f.side == "BUY"]
    assert buys, "reversal BUY should fill"
    sig = [s for s in _signals(k) if s.metadata.get("phase") == "reversal"]
    assert sig
    assert sig[0].metadata.get("tp") == 118.0    # target = leg POC
```
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_valentini_leg_anchor.py::test_reversal_fires_to_poc_after_profit -q`
Expected: FAIL — no SELL fill (reversal not implemented).

- [ ] **Step 3: Add day-PnL state and reset on session change**

In the constructor internal-state block (after `self._leg_start_idx: int = 0`), add:

```python
        self._day_pnl: float = 0.0        # realized PnL today (reversal gate)
```

In `on_candle_closed`, inside the `if key != self._session_key:` branch (after `self._leg_start_idx = 0`), add:

```python
            self._day_pnl = 0.0
```

- [ ] **Step 4: Track realized PnL in `_exit`**

At the top of `_exit` (after `act = self._active`), add:

```python
        if act["side"] == "BUY":
            self._day_pnl += (price - act["entry"]) * act["qty"]
        else:
            self._day_pnl += (act["entry"] - price) * act["qty"]
```

- [ ] **Step 5: Add `_maybe_reverse` and `_emit_reversal`**

Insert after `_structure_broken`:

```python
    def _maybe_reverse(self, event) -> bool:
        """Secondary mean-reversion setup (Fabio model §6): overextension +
        absorption at the extreme + response back toward leg POC. Only armed
        after a profitable day (``_day_pnl > 0``). Returns True when it
        emitted a reversal (caller then skips the continuation chain)."""
        if self._active is not None or self._pending is not None:
            return False
        if self._day_pnl <= 0:
            return False
        p = self._profile
        if p is None or p.poc <= 0:
            return False
        close = float(event.close)
        step = self._step
        poc = p.poc
        window_len = len(self._rows)
        recent = [a for a in self._absorptions
                  if a.bar_index >= window_len - self.abs_lookback]
        if not recent:
            return False
        a = recent[-1]
        if a.side == "SELL" and close > poc + self.reverse_extension_mult * step:
            # price absorbed at the high extreme, now responding back down
            if close < a.price:
                entry = float(event.close)
                sl = a.price + step
                if sl > entry:
                    self._emit_reversal(event, "SELL", entry, sl, poc, a)
                    return True
        elif a.side == "BUY" and close < poc - self.reverse_extension_mult * step:
            if close > a.price:
                entry = float(event.close)
                sl = a.price - step
                if sl < entry:
                    self._emit_reversal(event, "BUY", entry, sl, poc, a)
                    return True
        return False

    def _emit_reversal(self, event, side: str, entry: float, sl: float,
                       poc: float, absorption) -> None:
        """Size a reversal fade: risk-budget sizing, MARKET entry, SL at the
        extension extreme, TP = the leg POC."""
        if self._active is not None or self._pending is not None:
            return
        risk = float(self.ctx.account.balance) * (self.risk_per_trade_pct / 100.0)
        per_unit = abs(entry - sl)
        qty = int(risk / per_unit) if per_unit > 0 else 1
        qty = max(self.lot_size, (qty // self.lot_size) * self.lot_size)
        self._pending = {"symbol": event.symbol, "side": side, "entry": entry,
                         "sl": sl, "tp": poc, "qty": qty, "impulse_volume": 0.0}
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange, side=side,
            quantity=qty, price=0.0,  # MARKET
            reference_price=float(event.close),
            sl=sl, tp=poc, rr=0.0, phase="reversal",
            absorption=absorption.side,
            target="reversal_poc", prior_poc=self._prior_poc,
            session_poc=poc,
        )
        if self.ctx.mode != "live" and self._active is None:
            self._pending = None
```

- [ ] **Step 6: Dispatch reversal before the continuation chain**

In `on_candle_closed`, replace the tail (currently `if not self._in_session(event.ts): return` then `self._update_phase(event)`) with:

```python
        if not self._in_session(event.ts):
            return
        if self._maybe_reverse(event):
            return
        self._update_phase(event)
```

- [ ] **Step 7: Run the reversal tests + full suites**

Run: `python -m pytest tests/test_valentini_leg_anchor.py -q`
Expected: all pass (14 tests).

Run: `python -m pytest tests/test_valentini_strategy.py -q`
Expected: `24 passed`.

- [ ] **Step 8: Commit**

```bash
git add ntrade/engines/strategies.py tests/test_valentini_leg_anchor.py
git commit -m "feat: pnl-gated valentini reversal to leg POC"
```

---

### Task 6: Full suite + parity verification

**Files:**
- Test: run all relevant suites.

- [ ] **Step 1: Run strategy + parity + wiring**

Run: `python -m pytest tests/test_valentini_strategy.py tests/test_valentini_leg_anchor.py tests/test_valentini_live_wiring.py tests/test_zero_parity_across_modes.py -q`
Expected: all pass. If a wiring test fails on a VAL/VAH or signal expectation, adjust its synthetic frame (test file only) so the setup produces a clear directional structure — the sanctioned fixture tuning.

- [ ] **Step 2: Run the wider engine/risk suites**

Run: `python -m pytest tests/test_engine_pipeline.py tests/test_strategy_runner.py tests/test_integration_pipeline.py tests/test_event_traceability.py tests/test_risk_breakers.py tests/test_backtest_risk_breaker.py -q`
Expected: all pass.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q -p no:cacheprovider --ignore=tests/test_live_ws.py`
Expected: all pass (baseline 1036 + new tests).

- [ ] **Step 4: Commit any fixture adjustments (only if Step 1/2/3 required them)**

```bash
git add tests/
git commit -m "test: tune fixtures for direction-gated valentini setups"
```

## Self-review notes

- **Spec coverage:** Direction gate (spec §1) = Tasks 1-3. Auction-following trail (spec §5) = Task 4. Reversal (spec §6) = Task 5. Gap register rows (Direction gate, fixed-RM TP → trail, no reversal) all closed. LVN/HVN + true aggression remain documented limitations (spec §8), not in scope. ✔
- **Placeholder scan:** every code step carries full blocks; no TBDs. ✔
- **Type consistency:** `swing_bias` defined in Task 1, used in Task 2; `_direction`/`_volume_supports` defined in Task 2, used in Tasks 3; trail helpers defined in Task 4 used within Task 4; `_day_pnl`/`_maybe_reverse` defined in Task 5 used within Task 5. Knob names match the Global Constraints exactly. ✔
- **Laziness:** `swing_bias` is the only new helper; trail is a swing-pivot ratchet (chandelier deferred, `ponytail:` comment); reversal reuses `detect_absorptions` and the existing `_emit`/pending pattern. ✔
- **Zero-parity:** strategy-only changes; fills remain MARKET at `reference_price`; the synthetic parity frame has no impulse and a clear uptrend, so `_direction` returns BUY and the entry at `111.0` is preserved — verified in Task 6. ✔
- **Reversal parity:** `_day_pnl` is 0 in backtest until a profitable trade closes, so reversal cannot fire on the parity frame's first setup (it needs a prior win) — no parity divergence. ✔