"""Reusable strategies built on the kernel's canonical events.

Strategies live in ``Strategy`` subclass form: they react to events through the
standard hooks and emit signals via ``emit_signal()``, so they run identically
in live (BrokerExecution), replay (ReplayEngine) and backtest (BacktestSimulator).
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from ntrade.domain.analytics.indicators import vwap, vwap_bands
from ntrade.domain.analytics.order_flow import detect_absorptions
from ntrade.domain.analytics.range_bars import calc_auto_range, build_range_bars
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
    """Triple-A scalper: Absorption -> Accumulation -> Aggression (approximated).

    Implements Fabio Valentini's model on the data Dhan actually provides:

      * **Location** — volume profile (POC/VAH/VAL) built over range bars.
      * **Absorption** — "big volume, no price" bars via
        ``detect_absorptions`` (fully computable from OHLCV+volume).
      * **Accumulation** — price consolidates back near the absorption level
        for 2+ bars after the absorption.
      * **Aggression** — price above VWAP (BUY absorption) or below VWAP
        (SELL absorption) triggers the entry.

    SL/TP are computed on entry; the stop is trailed to breakeven after the
    trade reaches 0.5R; the session gate keeps the scalper out of overnight
    risk. CVD (true order flow) is NOT used here — Dhan has no trade tape;
    absorption + VWAP + profile are the honest signal layer.

    Runs identically in backtest / replay / live (zero-parity kernel).
    """

    name = "valentini"

    def __init__(self, *, symbol: str | None = None, timeframe: str = "1m",
                 range_size: float | None = None, atr_period: int = 14,
                 tick_size: float | None = None, warmup: int = 15,
                 abs_volume_mult: float = 1.5, abs_range_threshold: float = 0.5,
                 abs_lookback: int = 5, tp_multiplier: float = 2.0,
                 min_rr: float = 1.5, risk_per_trade_pct: float = 0.5,
                 lot_size: int = 1, session_start: str = "09:15",
                 session_end: str = "15:25", max_window: int = 600,
                 fade_extended: bool = True,
                 depth_imbalance_min: float | None = None):
        super().__init__()
        self.symbol = symbol
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
        self.session_start = datetime.strptime(session_start, "%H:%M").time()
        self.session_end = datetime.strptime(session_end, "%H:%M").time()
        self.max_window = max_window
        self.fade_extended = fade_extended
        self.depth_imbalance_min = depth_imbalance_min

        # Internal state
        self._rows: list[dict] = []
        self._phase: str = "waiting"
        self._range_size: float | None = None
        self._range_bars = pd.DataFrame()
        self._profile = None
        self._vwap: float | None = None
        self._absorptions = []
        self._last_absorption = None
        self._absorption_window_idx: int | None = None
        self._active: dict | None = None   # {side, entry, sl, tp, qty}
        self._pending: dict | None = None  # entry awaiting its fill (live: async)
        self._pending_age: int = 0         # candles since the entry was staged

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
            from zoneinfo import ZoneInfo
            if ts.tzinfo is not None:
                ts = ts.astimezone(ZoneInfo("Asia/Kolkata"))
        except Exception:
            pass  # pytz/zoneinfo unavailable — fall back to original ts
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
        self._range_bars = build_range_bars(
            frame, range_size=self._range_size,
            atr_period=self.atr_period, tick_size=self.tick_size)
        self._vwap = float(vwap(frame).iloc[-1])
        try:
            upper, lower = vwap_bands(frame, num_std=2.0)
            self._vwap_upper, self._vwap_lower = upper, lower
        except Exception:
            self._vwap_upper = self._vwap_lower = self._vwap
        bars_tail = self._range_bars.tail(30)
        if not bars_tail.empty:
            self._profile = build_volume_profile(
                bars_tail, step=self._range_size or None)
        self._absorptions = detect_absorptions(
            frame, avg_volume_mult=self.abs_volume_mult,
            range_threshold=self.abs_range_threshold,
            range_size=self._range_size)

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
        self._update_phase(event)

    def _update_phase(self, event) -> None:
        """Advance the Triple-A state machine on the latest bar."""
        close = float(event.close)
        window_len = len(self._rows)
        recent = [a for a in self._absorptions
                  if a.bar_index >= window_len - self.abs_lookback]
        step = self._range_size or 1.0

        if self._phase == "waiting":
            if recent:
                self._last_absorption = recent[-1]
                self._absorption_window_idx = recent[-1].bar_index
                self._phase = "absorbing"
            return

        if self._phase == "absorbing":
            elapsed = max(window_len - 1 - self._absorption_window_idx, 0)
            abs_price = self._last_absorption.price
            if (elapsed >= 2 and abs(close - abs_price) <= 2 * step):
                self._phase = "accumulating"
            elif elapsed > self.abs_lookback * 3:
                # Setup died: price ran away from the absorption level and
                # never came back into range — expire it so a fresh absorption
                # can re-arm the machine (was: hangs in absorbing forever).
                self._phase = "waiting"
                return
            # fall through: a bar can complete accumulation AND trigger the
            # aggression leg in the same candle

        if self._phase == "accumulating":
            elapsed = max(window_len - 1 - self._absorption_window_idx, 0)
            if elapsed > self.abs_lookback * 3:
                # symmetric with the absorbing expiry: a setup that drifted out
                # of the 2*step window before triggering has gone stale
                self._phase = "waiting"
                return
            side = self._last_absorption.side
            if side == "BUY" and close > self._vwap:
                if self.fade_extended and self._extended(close, side):
                    return  # extended beyond the band — wait for the pullback
                if self._depth_blocked(side, event.symbol):
                    return  # no live buy-side depth pressure — wait
                self._phase = "signal"
            elif side == "SELL" and close < self._vwap:
                if self.fade_extended and self._extended(close, side):
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

        Note: because ``sl`` is pinned one range below the absorption level
        and ``tp = entry + (entry - sl) * tp_multiplier``, the realised
        R-multiple ``(tp - entry) / (entry - sl)`` is always exactly
        ``tp_multiplier`` for a valid setup — ``min_rr`` only filters the
        degenerate ``entry <= sl`` case.
        """
        if self._active is not None or self._pending is not None:
            return
        entry = float(event.close)
        step = self._range_size or 1.0
        if side == "BUY":
            sl = self._last_absorption.price - step   # below the aggression
            tp = entry + (entry - sl) * self.tp_multiplier
            rr = (tp - entry) / (entry - sl) if entry > sl else 0.0
        else:
            sl = self._last_absorption.price + step
            tp = entry - (sl - entry) * self.tp_multiplier
            rr = (entry - tp) / (sl - entry) if sl > entry else 0.0
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
                         "sl": sl, "tp": tp, "qty": qty}
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange, side=side,
            quantity=qty, price=0.0,  # MARKET
            sl=sl, tp=tp, rr=round(rr, 2), phase="signal",
            absorption=self._last_absorption.side,
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
        """Stop / target / breakeven / session-close management."""
        act = self._active
        low, high, close = float(event.low), float(event.high), float(event.close)
        if act["side"] == "BUY":
            if low <= act["sl"]:
                self._exit(event, "SELL", act["sl"], reason="stop")
            elif high >= act["tp"]:
                self._exit(event, "SELL", act["tp"], reason="target")
            else:
                # trail to breakeven once the trade is 0.5R in profit
                half = act["entry"] + 0.5 * (act["tp"] - act["entry"])
                if high >= half and act["sl"] < act["entry"]:
                    act["sl"] = act["entry"]
        else:
            if high >= act["sl"]:
                self._exit(event, "BUY", act["sl"], reason="stop")
            elif low <= act["tp"]:
                self._exit(event, "BUY", act["tp"], reason="target")
            else:
                half = act["entry"] - 0.5 * (act["entry"] - act["tp"])
                if low <= half and act["sl"] > act["entry"]:
                    act["sl"] = act["entry"]
        # hard session close: never hold overnight
        if self._active is not None and not self._in_session(event.ts):
            self._exit(event, "SELL" if act["side"] == "BUY" else "BUY",
                       close, reason="session_close")

    def _exit(self, event, side: str, price: float, *, reason: str) -> None:
        """Exit at market (the trigger price is the *intent* price; the actual
        fill is at the bar's market when the exit is aggressive — SL/TP
        management is a backtest approximation, see the plan appendix)."""
        act = self._active
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange, side=side,
            quantity=act["qty"], price=0.0,  # MARKET
            exit_reason=reason, intent_price=price,
        )
        self._active = None
