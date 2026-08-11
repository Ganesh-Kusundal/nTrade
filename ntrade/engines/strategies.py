"""Reusable strategies built on the kernel's canonical events.

Strategies live in ``Strategy`` subclass form: they react to events through the
standard hooks and emit signals via ``emit_signal()``, so they run identically
in live (BrokerExecution), replay (ReplayEngine) and backtest (BacktestSimulator).
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from ntrade.domain.analytics.indicators import atr, vwap, vwap_bands
from ntrade.domain.analytics.order_flow import detect_absorptions, cvd_from_ohlcv
from ntrade.domain.analytics.range_bars import (
    build_range_bars, calc_auto_range, swing_bias)
from ntrade.domain.analytics.volume_profile import build_volume_profile
from ntrade.engines.strategy_engine import Strategy

logger = logging.getLogger("ntrade.strategy.valentini")


class EmaCrossStrategy(Strategy):
    """EMA fast/slow crossover — the canonical momentum strategy.

    Golden cross (fast EMA crosses above slow) → BUY; death cross (fast crosses
    below slow) → SELL. Position-aware so it holds one direction at a time
    (reverses instead of stacking) and only acts on an actual crossing, not a
    crossover that keeps its direction.

    Reads the EMA values from the indicator bundle projected onto the instrument
    by the IndicatorEngine (needs ``compute_bundle(ema_periods=(fast, slow))``),
    falling back to computing the EMAs itself when the bundle is missing.
    """

    name = "ema_cross"

    def __init__(self, fast: int = 9, slow: int = 21, quantity: int = 5,
                 symbol: str | None = None):
        super().__init__()
        self.fast = int(fast)
        self.slow = int(slow)
        self.quantity = int(quantity)
        self.symbol = symbol  # None = trade every candle
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    # ------------------------------------------------------------------ hook
    def on_candle_closed(self, event) -> None:
        if self.symbol is not None and event.symbol != self.symbol:
            return
        bundle = self.ctx.instrument(event.symbol)._indicators
        fast = bundle.get(f"ema_{self.fast}")
        slow = bundle.get(f"ema_{self.slow}")
        if fast is None or slow is None:
            return  # warm-up: not enough candles for both EMAs yet
        if self._prev_fast is None or self._prev_slow is None:
            self._prev_fast, self._prev_slow = fast, slow
            return  # need one prior reading to detect a crossing

        crossed_up = self._prev_fast <= self._prev_slow and fast > slow
        crossed_down = self._prev_fast >= self._prev_slow and fast < slow
        self._prev_fast, self._prev_slow = fast, slow
        if not crossed_up and not crossed_down:
            return

        position = self.ctx.portfolio.position(event.symbol)
        qty = position.quantity if position is not None else 0
        # Always-in-market with reversal, never stacking: BUY covers flat + short
        # entries (golden cross), SELL covers flat + long exits (death cross).
        if crossed_up and qty <= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=event.close)
        elif crossed_down and qty >= 0:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="SELL", quantity=self.quantity, price=event.close)


class ValentiniScalper(Strategy):
    """Triple-A scalper (build-guide contract, on Dhan's OHLCV/depth data).

    Guide Triple-A: Absorption -> Accumulation -> Aggression. Every step below
    is executed (earlier versions computed the volume profile and then ignored
    it). On the data Dhan provides:

      * **Absorption** — "big volume, no price" bars via ``detect_absorptions``
        (fully computable from OHLCV+volume). Arms only at the value edge:
        BUY near session VAL, SELL near session VAH.
      * **Accumulation** — price consolidates back near the session POC for
        2+ bars (guide: "within 2 range steps of POC").
      * **Aggression** — price above VWAP (BUY) / below VWAP (SELL) AND the
        trailing CVD proxy agrees (close-vs-open delta), NOT mid-value inside
        the value area (balance = stay flat). Optional L2 depth filter still
        applies when ``depth_imbalance_min`` is set.

    SL = VAL − step (long) / VAH + step (short). TP prefers the prior session
    POC when its R:R clears ``min_rr``, else the position is a runner (no hard
    target) that follows the auction: the stop trails under swing pivots and
    exits on a structure break or volume-price divergence. The session gate
    keeps the scalper flat overnight. True order flow (tape, footprint) is
    unavailable on Dhan — the CVD here is an OHLCV proxy, not tape; it is
    labelled as such.
    """

    name = "valentini"

    def __init__(self, *, symbol: str | None = None, exchange: str = "NSE",
                 timeframe: str = "1m", range_size: float | None = None,
                 atr_period: int = 14, tick_size: float | None = None,
                 warmup: int = 15, abs_volume_mult: float = 1.5,
                 abs_range_threshold: float = 0.5, abs_lookback: int = 5,
                 tp_multiplier: float = 2.0, min_rr: float = 1.5,
                 risk_per_trade_pct: float = 0.5, lot_size: int = 1,
                 session_start: str | None = None,
                 session_end: str | None = None, max_window: int = 600,
                 fade_extended: bool = True,
                 depth_imbalance_min: float | None = None,
                 require_cvd: bool = True, cvd_confirm_bars: int = 3,
                 leg_impulse_mult: float = 2.0,
                 accum_volume_mult: float = 1.5,
                 direction_volume_mult: float = 1.0,
                 trail_arm_mult: float = 1.0,
                 divergence_volume_mult: float = 0.6,
                 reverse_extension_mult: float = 2.0):
        super().__init__()
        self.symbol = symbol
        self.exchange = exchange
        self.timeframe = timeframe
        self.range_size = range_size
        self.atr_period = atr_period
        self.tick_size = tick_size
        self.warmup = warmup
        self.abs_volume_mult = abs_volume_mult
        self.abs_range_threshold = abs_range_threshold
        self.abs_lookback = abs_lookback
        self.tp_multiplier = tp_multiplier
        self.min_rr = min_rr
        self.risk_per_trade_pct = risk_per_trade_pct
        self.lot_size = max(1, int(lot_size))
        # Derive session hours from the exchange when the caller didn't pass
        # explicit times — NSE/NFO = 09:15-15:25, MCX = 09:00-23:25 (IST).
        # Without this, MCX contracts would only trade during NSE hours and
        # miss the entire 15:25-23:25 evening session.
        if session_start is None or session_end is None:
            from ntrade.domain.market_hours import session_open, session_close
            open_t = session_open(exchange)
            close_t = session_close(exchange)
            # Strategy uses 15:25 (5m before close) as its hard exit — never
            # hold into the final 5 minutes.
            from datetime import time as _time
            if close_t.minute >= 5:
                default_end = _time(close_t.hour, close_t.minute - 5)
                session_end = session_end or default_end.strftime("%H:%M")
            session_start = session_start or open_t.strftime("%H:%M")
        self.session_start = datetime.strptime(session_start, "%H:%M").time()
        self.session_end = datetime.strptime(session_end, "%H:%M").time()
        self.max_window = max_window
        self.fade_extended = fade_extended
        self.depth_imbalance_min = depth_imbalance_min
        self.require_cvd = require_cvd
        self.cvd_confirm_bars = max(1, int(cvd_confirm_bars))
        self.leg_impulse_mult = max(1.0, float(leg_impulse_mult))
        self.accum_volume_mult = max(0.0, float(accum_volume_mult))
        self.direction_volume_mult = max(0.0, float(direction_volume_mult))
        self.trail_arm_mult = max(0.0, float(trail_arm_mult))
        self.divergence_volume_mult = max(0.0, float(divergence_volume_mult))
        self.reverse_extension_mult = max(1.0, float(reverse_extension_mult))

        # ponytail: session-keyed profile + prior POC. Profile is the guide's
        # "location" — built from TODAY's rows (not tail(30)) so POC/VAH/VAL
        # reflect the session; prior_poc becomes the aggression target.
        self._session_key: str | None = None
        self._profile = None          # current-session VolumeProfile
        self._prior_poc: float | None = None

        # Internal state
        self._rows: list[dict] = []
        self._phase: str = "waiting"
        self._range_size: float | None = None
        self._range_bars = pd.DataFrame()
        self._vwap: float | None = None
        self._absorptions = []
        self._last_absorption = None
        self._absorption_window_idx: int | None = None
        self._active: dict | None = None   # {side, entry, sl, tp, qty}
        self._pending: dict | None = None  # entry awaiting its fill (live: async)
        self._pending_age: int = 0         # candles since the entry was staged
        self._atr: float = 0.0            # current ATR, recomputed per candle
        self._step: float = 1.0           # step = max(range_size, ATR) — ATR floor
        self._leg_start_idx: int = 0      # row index where the current impulse leg began
        self._day_pnl: float = 0.0        # realized PnL today (reversal gate)

    # ------------------------------------------------------------------ hooks
    @property
    def phase(self) -> str:
        """Triple-A phase: waiting | absorbing | accumulating | signal."""
        return self._phase

    def _in_session(self, ts) -> bool:
        if ts is None:
            return True
        # Normalize to IST — live feed timestamps may be UTC; backtest/replay
        # timestamps are IST-naive. Without conversion, a 10:00 UTC event
        # (= 15:30 IST, after the gate) would be evaluated as 10:00 (in session)
        # — a silent out-of-hours entry on live data.
        try:
            if ts.tzinfo is not None:
                ts = ts.astimezone(ZoneInfo("Asia/Kolkata"))
        except Exception:
            pass  # zoneinfo unavailable — fall back to original ts
        t = ts.time()
        return self.session_start <= t <= self.session_end

    def _extended(self, close: float, side: str) -> bool:
        """True when the trigger bar has chased beyond the VWAP ±2σ band.

        The VWAP bands give the overbought/oversold context (entry at the
        value area vs VWAP bias): a BUY whose close is beyond the upper band
        is an extended move — poor R:R to chase — so the setup waits for a
        pullback instead. NaN / fallback bands disable the filter.
        """
        upper, lower = self._vwap_upper, self._vwap_lower
        if pd.isna(upper) or pd.isna(lower) or not upper > lower:
            return False
        return close > upper if side == "BUY" else close < lower

    def _session_key_of(self, ts) -> str:
        """IST date key for the session grouping (mirror of the UI istDateKey)."""
        if ts is None:
            return ""
        try:
            if ts.tzinfo is not None:
                ts = ts.astimezone(ZoneInfo("Asia/Kolkata"))
        except Exception:
            pass
        return ts.strftime("%Y-%m-%d")

    def _value_edge_ok(self, side: str, price: float, step: float) -> bool:
        """Absorption must sit at the value edge (guide: VAL for longs, VAH
        for shorts) — not mid-value. No profile yet => allow (warm-up)."""
        p = self._profile
        if p is None:
            return True
        if side == "BUY":
            return abs(price - p.val) <= 2 * step
        return abs(price - p.vah) <= 2 * step

    def _in_balance(self, close: float) -> bool:
        """Mid-value inside the value area = balance: skip aggression."""
        p = self._profile
        if p is None or p.vah <= p.val:
            return False
        if not (p.val <= close <= p.vah):
            return False
        return abs(close - p.poc) / (p.vah - p.val) < 0.25

    def _cvd_agrees(self, side: str) -> bool:
        """Trailing CVD proxy must confirm the VWAP direction (OHLCV, not tape)."""
        if not self.require_cvd:
            return True
        if pd.isna(self._cvd):
            return False
        return self._cvd >= 0 if side == "BUY" else self._cvd <= 0

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

    def on_candle_closed(self, event) -> None:
        if self.symbol is not None and event.symbol != self.symbol:
            return
        if event.timeframe != self.timeframe:
            return
        self._rows.append({
            "timestamp": event.ts, "open": event.open, "high": event.high,
            "low": event.low, "close": event.close, "volume": event.volume,
        })
        if len(self._rows) > self.max_window:
            del self._rows[:len(self._rows) - self.max_window]
        if len(self._rows) < self.warmup:
            return

        frame = pd.DataFrame(self._rows)
        # Rebuild the analytics on the trailing 1m window (range bars give
        # the location; VWAP gives the direction filter).
        self._range_size = self.range_size or calc_auto_range(
            frame, atr_period=self.atr_period, tick_size=self.tick_size)
        # ATR floor: step can never be tighter than 1x ATR (low-tick IL&O
        # contracts would otherwise get sub-ATR stops from a small explicit
        # range_size). calc_auto_range already yields ~1x ATR, so this only
        # clamps explicit range_size values.
        a = atr(frame, self.atr_period)
        self._atr = 0.0
        if len(a) and pd.notna(a.iloc[-1]) and a.iloc[-1] > 0:
            self._atr = float(a.iloc[-1])
        self._step = max(self._range_size or 1.0, self._atr or 0.0)
        # Session key (IST date) drives the location profile. On a new day we
        # stash the prior-session POC as the aggression target and drop the
        # stale profile so today's value area is rebuilt from scratch.
        key = self._session_key_of(event.ts)
        if key != self._session_key:
            if self._profile is not None:
                self._prior_poc = self._profile.poc or self._prior_poc
            prev_key = self._session_key
            self._session_key = key
            self._profile = None
            self._leg_start_idx = 0
            if prev_key is not None:
                # A genuine session rollover resets the realized day PnL.
                # The FIRST session must not wipe a white-box _day_pnl (or
                # the PnL gate would never arm on warm-up day one).
                self._day_pnl = 0.0
        step = self._step
        self._range_bars = build_range_bars(
            frame, range_size=self._range_size,
            atr_period=self.atr_period, tick_size=self.tick_size)
        # Advance the leg anchor: the last 1m candle with span >= impulse
        # threshold starts a fresh leg. Clamp to the windowed frame.
        imp_thr = self.leg_impulse_mult * self._step
        imp_idx = -1
        for i in range(len(frame) - 1, -1, -1):
            row = frame.iloc[i]
            if (float(row["high"]) - float(row["low"])) >= imp_thr:
                imp_idx = i
                break
        if imp_idx >= 0:
            self._leg_start_idx = imp_idx
        self._leg_start_idx = min(self._leg_start_idx, len(frame) - 1)
        self._vwap = float(vwap(frame).iloc[-1])
        try:
            upper, lower = vwap_bands(frame, num_std=2.0)
            self._vwap_upper, self._vwap_lower = upper, lower
        except Exception:
            self._vwap_upper = self._vwap_lower = self._vwap
        # Guide "location": POC/VAH/VAL over the CURRENT LEG, not the whole
        # session. A new impulse leg starts at the last 1m candle whose span
        # >= leg_impulse_mult * self._step (the ATR-floored step, so it binds
        # even when range_size < ATR). Range-bar spans can't drive this (they
        # close at range_size by construction), so the impulse test is on 1m
        # candle span. ponytail: single-candle span heuristic; a real
        # leg detector (multi-candle momentum) would be over-engineering here.
        if not frame.empty:
            leg_frame = frame.iloc[self._leg_start_idx:]
            if self._leg_start_idx > 0 and not leg_frame.empty:
                self._profile = build_volume_profile(
                    leg_frame, step=self._step)
            else:
                self._profile = build_volume_profile(
                    frame, step=self._step)
        self._absorptions = detect_absorptions(
            frame, avg_volume_mult=self.abs_volume_mult,
            range_threshold=self.abs_range_threshold,
            range_size=self._range_size)
        # CVD proxy (OHLCV): signed volume delta over the confirm window. The
        # aggression leg requires it to agree with the VWAP direction.
        self._cvd = float("nan")
        if self.require_cvd and not frame.empty:
            cvd = cvd_from_ohlcv(frame)
            if len(cvd) >= self.cvd_confirm_bars:
                self._cvd = float(cvd.tail(self.cvd_confirm_bars).diff().sum())

        # Expire a pending entry the broker never filled (live: rejected/
        # lost order) so the strategy can re-arm instead of locking out
        # forever. Backtest/replay clear _pending synchronously in
        # _emit_entry, so this only bites in live mode.
        if self._pending is not None and self._active is None:
            self._pending_age += 1
            if self._pending_age > 3:
                logger.info("valentini pending entry expired (never filled)")
                self._pending = None
                self._pending_age = 0
        else:
            self._pending_age = 0

        # Trade management first (SL/TP/breakeven/session-close).
        if self._active is not None:
            self._manage_exit(event)
            return
        if not self._in_session(event.ts):
            return
        if self._maybe_reverse(event):
            return
        self._update_phase(event)

    def _update_phase(self, event) -> None:
        """Advance the Triple-A state machine on the latest bar."""
        close = float(event.close)
        frame = pd.DataFrame(self._rows)
        window_len = len(self._rows)
        recent = [a for a in self._absorptions
                  if a.bar_index >= window_len - self.abs_lookback]
        step = self._step

        if self._phase == "waiting":
            # Only arm an absorption that sits at the value edge (VAL/VAH),
            # not one floating mid-range — guide "location" rule.
            armed = [a for a in recent if self._value_edge_ok(a.side, a.price, step)]
            if armed:
                self._last_absorption = armed[-1]
                self._absorption_window_idx = armed[-1].bar_index
                self._phase = "absorbing"
            return

        if self._phase == "absorbing":
            elapsed = max(window_len - 1 - self._absorption_window_idx, 0)
            # Accumulation is "price near the session POC" (guide §4.1), not
            # near the absorption price.
            poc = self._profile.poc if self._profile else close
            recent_vol = float(frame["volume"].iloc[-2:].sum())
            prior_vol = frame["volume"].iloc[:-2]
            avg_vol = float(prior_vol.median()) if len(prior_vol) else 0.0
            # Volume confirmation: the move back to the POC must carry real
            # participation (guide §4.1), not a dead drift. NaN/empty guard:
            # no prior history means no volume test to fail. Median baseline
            # (not mean) — the absorption spike would otherwise inflate the
            # average and reject normal follow-through volume.
            vol_ok = (avg_vol <= 0
                      or recent_vol >= self.accum_volume_mult * avg_vol)
            if (elapsed >= 2 and abs(close - poc) <= 2 * step and vol_ok):
                self._phase = "accumulating"
            elif elapsed > self.abs_lookback * 3:
                # Setup died: price ran away from value and never came back
                # into range — expire so a fresh absorption can re-arm.
                self._phase = "waiting"
                return
            # fall through: a bar can complete accumulation AND trigger the
            # aggression leg in the same candle

        if self._phase == "accumulating":
            elapsed = max(window_len - 1 - self._absorption_window_idx, 0)
            if elapsed > self.abs_lookback * 3:
                # symmetric expiry: a setup that drifted out of the 2*step
                # window before triggering has gone stale
                self._phase = "waiting"
                return
            side = self._last_absorption.side
            # Aggression: VWAP bias + CVD proxy agreement + not mid-value
            # balance. Extended/ depth gates are layered on top.
            if self._in_balance(close):
                return  # balance — stay flat, no signal
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
            if self._phase == "signal":
                self._emit_entry(event, side)
                self._phase = "waiting"  # reset after one signal per setup

    def _depth_blocked(self, side: str, symbol: str) -> bool:
        """Optional live-only depth-imbalance confidence gate.

        With ``depth_imbalance_min`` set, a BUY entry additionally requires
        bid_ask_imbalance >= min (buy pressure) and a SELL requires
        imbalance <= -min (sell pressure). The filter is *skipped entirely*
        when no live depth is present (backtest/replay/paper have an empty
        order book), preserving zero-parity — it only tightens live NFO
        entries where Full(21) depth streams.
        """
        if self.depth_imbalance_min is None:
            return False
        inst = self.ctx.instrument(symbol)
        depth = None
        try:
            depth = inst.market.depth() if inst is not None else None
        except Exception:  # noqa: BLE001 — missing depth never blocks a signal
            depth = None
        if depth is None or not depth.bids or not depth.asks:
            return False  # no live book — no filter
        imbalance = float(depth.bid_ask_imbalance() or 0.0)
        if side == "BUY":
            return imbalance < self.depth_imbalance_min
        return imbalance > -self.depth_imbalance_min

    def _emit_entry(self, event, side: str) -> None:
        """Size the position, compute SL/TP, and emit the entry signal.

        Entries are MARKET (``price=0`` -> OrderEngine builds a MARKET
        intent) so an aggressive scalper entry fills on the signal bar
        instead of being stranded by a LIMIT that the rally leaves behind
        (bar-aware backtest fills only fill when traded through).

        Stop: VAL − step (long) / VAH + step (short) — guide §4.1. Target:
        prefer the prior session's POC (Fabio's "target the POC") when its
        R:R clears ``min_rr``; otherwise the position is a runner (``tp=None``)
        that follows the auction via _manage_exit (swing-pivot trail +
        divergence/structure exits). ``min_rr`` filters degenerate setups.
        """
        if self._active is not None or self._pending is not None:
            return
        entry = float(event.close)
        step = self._step
        leg = pd.DataFrame(self._rows).iloc[self._leg_start_idx:]
        impulse_volume = float(leg["volume"].sum()) if not leg.empty else 0.0
        p = self._profile
        val = p.val if p else None
        vah = p.vah if p else None
        # SL pinned at the value edge (one step outside) — never inside value.
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
        risk = float(self.ctx.account.balance) * (self.risk_per_trade_pct / 100.0)
        per_unit = abs(entry - sl)
        qty = int(risk / per_unit) if per_unit > 0 else 1
        qty = max(self.lot_size, (qty // self.lot_size) * self.lot_size)
        # Stage the entry as pending; _active arms in on_order_filled when the
        # entry fill actually lands. Arming here would manage exits for a
        # position that was never opened (phantom naked exit) if a risk check
        # rejected the entry — and a synchronous portfolio read would break
        # live mode, where broker fills arrive asynchronously via websocket.
        self._pending = {"symbol": event.symbol, "side": side, "entry": entry,
                         "sl": sl, "tp": tp, "qty": qty,
                         "impulse_volume": impulse_volume}
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange, side=side,
            quantity=qty, price=0.0,  # MARKET
            reference_price=float(event.close),  # bar close from CandleClosedEvent
            sl=sl, tp=tp, rr=round(rr, 2), phase="signal",
            absorption=self._last_absorption.side,
            sl_source="val_vah" if p else "absorption",
            target=target,
            prior_poc=self._prior_poc, session_poc=p.poc if p else None,
        )
        # Synchronous modes (backtest/replay): by the time emit_signal returns
        # a rejected entry has produced no fill, so _active is still unarmed —
        # drop the pending so a later valid setup can re-enter. A stale
        # pending would otherwise lock the strategy out forever through the
        # ``_pending`` guard above. Live mode fills asynchronously: keep the
        # pending here; on_candle_closed expires it if it never fills.
        if self.ctx.mode != "live" and self._active is None:
            self._pending = None

    def on_order_filled(self, event) -> None:
        """Arm the managed trade once the pending entry actually fills.

        Zero-parity across modes: in backtest/replay the fill is published
        synchronously inside ``emit_signal``; live it arrives when the broker's
        fill event lands. A rejected or never-filled entry leaves ``_active``
        unarmed, so ``_manage_exit`` can never act on a phantom position.
        """
        if self._pending is None:
            return
        if (event.symbol != self._pending["symbol"]
                or event.side != self._pending["side"]):
            return
        filled = int(event.quantity)
        if filled <= 0:
            return
        self._active = {k: v for k, v in self._pending.items() if k != "symbol"}
        self._active["qty"] = filled  # exit only what actually filled
        self._pending = None

    def _manage_exit(self, event) -> None:
        """Auction-following trail / stop / target / session-close management."""
        act = self._active
        low, high, close = float(event.low), float(event.high), float(event.close)
        risk = abs(act["entry"] - act["sl"])
        side = act["side"]
        # Hard session close FIRST: the session gate is absolute ("never hold
        # past close") and must win over the auction exits, which are
        # conditional on price/volume narrative. Without this ordering a
        # post-session candle would exit "structure_break" instead of
        # "session_close".
        if not self._in_session(event.ts):
            self._exit(event, "SELL" if side == "BUY" else "BUY",
                       close, reason="session_close")
            return
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

    def _exit(self, event, side: str, price: float, *, reason: str) -> None:
        """Exit at market (the trigger price is the *intent* price; the actual
        fill is at the bar's market when the exit is aggressive — SL/TP
        management is a backtest approximation, see the plan appendix)."""
        act = self._active
        if act["side"] == "BUY":
            self._day_pnl += (price - act["entry"]) * act["qty"]
        else:
            self._day_pnl += (act["entry"] - price) * act["qty"]
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange, side=side,
            quantity=act["qty"], price=0.0,  # MARKET
            reference_price=float(event.close),  # bar close from CandleClosedEvent
            exit_reason=reason, intent_price=price,
        )
        self._active = None
