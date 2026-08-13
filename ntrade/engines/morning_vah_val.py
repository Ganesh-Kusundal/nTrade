"""Morning VAH/VAL scalper — the Mukul Chowdhury "first 15 minutes" setup.

* **2-minute bars** are aggregated from the 1m close stream (Dhan has no
  native 2m) on an IST-aligned fixed bucket grid, so the Python engine and the
  UI's TS mirror bucket identically.
* **FRVP**: the volume profile over the first 15 minutes (09:15–09:30 IST) is
  built once at 09:30 and **frozen** — VAH/VAL are static for the morning.
* **Prior-day bias**: at each session rollover the previous day's close-vs-open
  decides the only tradeable direction (UP → longs only, DOWN → shorts only,
  SIDEWAYS → no trades).
* **Long**: prior-day UP, price fakes below VAL, a bullish reversal closes
  back above VAL. **Short**: prior-day DOWN, price tests VAH, a bearish
  reversal closes back below VAH.
* **Sizing**: risk-budget; 100% when the reversal sits at the 10/20 EMA
  cluster along with VAL, else 50% (or cluster-only with ``require_cluster``).
* **T1** = opposite VA level: book ~50% + breakeven (``book_partial=True``)
  or full-size breakeven ride (``book_partial=False`` halves the fill count
  → halves the statutory cost drag), then trail the runner
  (``trail_back`` bars back) toward the day high/low. Hard close at
  ``hard_close`` (default 11:00) and at the session end.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from ntrade.domain.analytics.indicators import atr, ema
from ntrade.domain.analytics.volume_profile import build_volume_profile
from ntrade.engines.strategy_engine import Strategy

logger = logging.getLogger("ntrade.strategy.morning_vah_val")

_IST = ZoneInfo("Asia/Kolkata")


def _ist_dt(ts) -> datetime | None:
    """Timestamp → IST tz-aware datetime.

    Naive timestamps are IST wall time (the repo's domain convention — the
    parquet store and the API both use it); tz-aware values are converted.
    """
    if ts is None:
        return None
    if ts.tzinfo is not None:
        try:
            return ts.astimezone(_IST)
        except Exception:  # noqa: BLE001 — broken tz never blocks the strategy
            pass
    try:
        return ts.replace(tzinfo=_IST)
    except Exception:  # noqa: BLE001
        return None


def _minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _epoch(dt: datetime) -> int | None:
    try:
        return int(dt.timestamp())
    except Exception:  # noqa: BLE001
        return None


def _hhmm_minutes(value: str) -> int:
    h, m = (int(part) for part in value.split(":", 1))
    return h * 60 + m


class MorningVAHVAL(Strategy):
    """Morning VAH/VAL scalper (see module docstring for the full contract)."""

    name = "morning_vah_val"

    # Cost-drag-tuned preset — sweep-validated over 54 days of BANKNIFTY AUG
    # futures (full-window zero-parity backtest with realistic STT/commission):
    # require_cluster + reversal margin + classic partial cut the net loss from
    # -12.89% to ~-0.3% while drawdown drops 13.19% -> 0.6% and win rate rises
    # to ~67% (6 round trips). On futures the residual drag is the statutory
    # STT on the ~0.19% of lot notional per round trip — the setup is
    # structurally cost-bound on index futures; trading it on options (STT on
    # premium only) removes the floor. Consumed by the paper trader, the CLI
    # backtest and the UI mirror so every surface runs the tuned config.
    #
    # NOTE: ``sl_pad`` is absolute price points calibrated for ~₹76k index-
    # futures notional (30 pts ≈ 0.04%). Callers trading other price scales
    # should scale it (the paper trader does, via the instrument's price).
    TUNED = {
        "require_cluster": True,
        "reversal_margin_pct": 0.001,
        "book_partial": True,
        "trail_back": 1,
        "sideway_threshold": 0.0015,
        "sl_pad": 30.0,
    }

    # Reference price for the tuned sl_pad (BANKNIFTY futures ~₹76k); the
    # paper trader scales sl_pad by price/TUNED_REF_PRICE for other symbols.
    TUNED_REF_PRICE = 76_500.0

    def __init__(self, *, symbol: str | None = None, exchange: str = "NFO",
                 timeframe: str = "1m", risk_per_trade_pct: float = 0.5,
                 lot_size: int = 1, session_start: str = "09:15",
                 profile_end: str = "09:30", entry_start: str = "09:30",
                 entry_end: str = "11:00", ema_fast: int = 10,
                 ema_slow: int = 20, ema_cluster_tol_pct: float = 0.1,
                 sideway_threshold: float = 0.0015, sl_pad: float = 0.0,
                 require_cluster: bool = False, book_partial: bool = True,
                 trail_back: int = 1, reversal_margin_pct: float = 0.0,
                 trail_mode: str = "candle", atr_mult: float = 2.0,
                 atr_period: int = 3, max_window: int = 6000):
        super().__init__()
        self.symbol = symbol
        self.exchange = exchange
        self.timeframe = timeframe
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.lot_size = max(1, int(lot_size))
        self.ema_fast = max(2, int(ema_fast))
        self.ema_slow = max(3, int(ema_slow))
        self.ema_cluster_tol_pct = max(0.0, float(ema_cluster_tol_pct))
        # Prior-day drift (close-vs-open, as a fraction) below this is
        # SIDEWAYS (no trades). 0.0015 = 0.15% — index futures move ~0.2-2%
        # daily, so a 5% bar would label every day sideways.
        self.sideway_threshold = max(0.0, float(sideway_threshold))
        self.sl_pad = float(sl_pad)
        # Tuning knobs for the cost-drag fight (all backward-compatible):
        # - require_cluster: ONLY take entries at the 10/20 EMA cluster (drop
        #   the 50% tier entirely — fewer, higher-quality trades).
        # - book_partial: at T1 either book half + breakeven (classic) or just
        #   move to breakeven and let the FULL position ride the trail
        #   (half the fills → half the statutory cost drag).
        # - trail_back: how many completed bars back the BE-phase ratchet
        #   looks (1 = previous bar, the classic tight trail; 2-3 = looser,
        #   lets winners extend toward day high/low).
        # - reversal_margin_pct: the reversal close must clear VAL/VAH by this
        #   fraction (0.0 = current behavior; >0 filters weak reversals).
        # - trail_mode: "candle" = classic per-bar ratchet (trail_back bars
        #   back); "chandelier" = ATR-based stop at day-extreme −/+ mult×ATR
        #   (wider: lets winners breathe through small pullbacks, at the cost
        #   of giving back more on reversals — a direct fight against the
        #   per-trade cost floor). atr_period defaults to 3 (2m bars) so the
        #   chandelier actually ratchets inside the 09:30-11:00 window — the
        #   sweep showed per=14 stalls it for the first ~15 buckets.
        #   Sweep result (32 configs + splits): the chandelier does NOT beat
        #   the candle ratchet on BANKNIFTY futures (best mult=0.75 ties it;
        #   wider stops give the winners back at the 11:00 hard close) — kept
        #   as an option, not the tuned default. ponytail: available-but-
        #   losing option (best ties candle at -0.27%, wider stops lose).
        #   Ceiling: futures STT floor + entry filter fixing trade count.
        #   Upgrade: re-sweep when options OHLCV (STT on premium) exists, or
        #   when an entry filter yields >=8-12 round trips.
        self.require_cluster = bool(require_cluster)
        self.book_partial = bool(book_partial)
        self.trail_back = max(1, int(trail_back))
        self.reversal_margin_pct = max(0.0, float(reversal_margin_pct))
        self.trail_mode = trail_mode
        self.atr_mult = max(0.5, float(atr_mult))
        self.atr_period = max(2, int(atr_period))
        self.max_window = max(120, int(max_window))
        self._session_start_min = _hhmm_minutes(session_start)
        self._profile_end_min = _hhmm_minutes(profile_end)
        self._entry_start_min = _hhmm_minutes(entry_start)
        self._entry_end_min = _hhmm_minutes(entry_end)
        self._hard_close_min = self._entry_end_min
        from ntrade.domain.market_hours import session_close
        close_t = session_close(exchange)
        self._session_end_min = close_t.hour * 60 + close_t.minute

        self._rows: list[dict] = []          # 1m bars (FRVP + prior-day bias)
        self._bars: list[dict] = []          # aggregated 2m bars
        self._pending: dict | None = None    # in-progress 2m bar
        self._pending_order: dict | None = None  # entry awaiting its fill
        self._profile = None                 # frozen morning VolumeProfile
        self._vah: float = 0.0
        self._val: float = 0.0
        self._poc: float = 0.0
        self._bias: str | None = None        # UP | DOWN | SIDEWAYS | None (day one)
        self._day_key: str | None = None
        self._active: dict | None = None
        self._day_pnl: float = 0.0

    # ------------------------------------------------------------- helpers
    @property
    def profile_ready(self) -> bool:
        return self._profile is not None

    @property
    def bias(self) -> str | None:
        return self._bias

    @property
    def vah(self) -> float:
        return self._vah

    @property
    def val(self) -> float:
        return self._val

    def _day_key_of(self, dt: datetime | None) -> str:
        return dt.strftime("%Y-%m-%d") if dt is not None else ""

    def _bucket(self, epoch: int) -> int:
        """IST-aligned 2m bucket (deterministic; identical in the UI mirror)."""
        return (epoch + 5.5 * 3600) // 120

    def _set_bias_from_rows(self, today_key: str) -> None:
        """Prior-day context: the last FULL prior-day's close-vs-open.

        Rows are grouped by IST date; the most recent group whose key differs
        from today decides the direction. Less than a full prior day (e.g. a
        data gap) leaves the bias unchanged.
        """
        groups: dict[str, list[dict]] = {}
        for r in self._rows:
            k = self._day_key_of(_ist_dt(r["ts"]))
            groups.setdefault(k, []).append(r)
        prior_keys = [k for k in groups if k and k != today_key]
        if not prior_keys:
            return
        prior = groups[prior_keys[-1]]
        if len(prior) < 2:
            return
        first_open = float(prior[0]["open"])
        last_close = float(prior[-1]["close"])
        if first_open <= 0:
            return
        rel = (last_close / first_open) - 1.0
        if rel > self.sideway_threshold:
            self._bias = "UP"
        elif rel < -self.sideway_threshold:
            self._bias = "DOWN"
        else:
            self._bias = "SIDEWAYS"

    def _ema_cluster(self, close: float) -> bool:
        """Reversal bar sits at the 10/20 EMA cluster (within tol% of both)."""
        if len(self._bars) < self.ema_slow:
            return False
        closes = pd.Series([float(b["close"]) for b in self._bars])
        frame = pd.DataFrame({"close": closes})
        fast = float(ema(frame, self.ema_fast).iloc[-1])
        slow = float(ema(frame, self.ema_slow).iloc[-1])
        if not (fast > 0 and slow > 0):
            return False
        tol = self.ema_cluster_tol_pct * close
        return abs(close - fast) <= tol and abs(close - slow) <= tol

    def _atr_now(self) -> float:
        """ATR over the completed 2m bars (0.0 until enough bars exist).

        Matches the ``atr`` indicator's own minimum: pandas ``ewm(
        min_periods=period)`` yields a value once ``period`` bars exist (TR
        row 0 is the finite ``high-low`` — the NaN prev-close terms are
        skipped by max), so the guard is ``len >= atr_period``, not +1.
        """
        if len(self._bars) < self.atr_period:
            return 0.0
        frame = pd.DataFrame([
            {k: float(b[k]) for k in ("high", "low", "close")}
            for b in self._bars
        ])
        a = atr(frame, self.atr_period)
        val = float(a.iloc[-1]) if len(a) and pd.notna(a.iloc[-1]) else 0.0
        return val if val > 0 else 0.0

    def _size(self, entry: float, sl: float, *, full: bool) -> int:
        risk = float(self.ctx.account.balance) * (self.risk_per_trade_pct / 100.0)
        risk = risk * (1.0 if full else 0.5)  # 100% sizing = full budget; else 50%
        per_unit = abs(entry - sl)
        if per_unit <= 0:
            return 0
        qty = int(risk / per_unit)
        qty = max(self.lot_size, (qty // self.lot_size) * self.lot_size)
        return qty

    # ------------------------------------------------------------- hooks
    def on_candle_closed(self, event) -> None:
        if self.symbol is not None and event.symbol != self.symbol:
            return
        if event.timeframe != self.timeframe:
            return
        ist = _ist_dt(event.ts)
        epoch = _epoch(ist) if ist is not None else None
        if ist is None or epoch is None:
            return
        minute = _minute_of_day(ist)
        row = {"ts": event.ts, "open": float(event.open), "high": float(event.high),
               "low": float(event.low), "close": float(event.close),
               "volume": int(event.volume or 0)}
        self._rows.append(row)
        if len(self._rows) > self.max_window:
            del self._rows[:len(self._rows) - self.max_window]

        # Session rollover: prior-day bias, force-close, fresh day state.
        key = self._day_key_of(ist)
        if key != self._day_key:
            if self._day_key is not None:
                self._set_bias_from_rows(key)
                if self._active is not None:
                    self._exit(event, "SELL" if self._active["side"] == "BUY" else "BUY",
                               float(event.close), reason="session_close")
                self._active = None
            self._day_key = key
            self._profile = None
            self._vah = self._val = self._poc = 0.0
            self._bars = []
            self._pending = None
            self._pending_order = None
            self._day_pnl = 0.0
            self._rows[:] = [self._rows[-1]]  # keep today's first bar

        # Freeze the morning FRVP once the 09:15–09:30 window has closed.
        if self._profile is None and minute >= self._profile_end_min:
            window = []
            for r in self._rows:
                r_ist = _ist_dt(r["ts"])
                if r_ist is not None:
                    m = _minute_of_day(r_ist)
                    if self._session_start_min <= m < self._profile_end_min:
                        window.append(r)
            if len(window) >= 2:
                frame = pd.DataFrame(window)
                prof = build_volume_profile(frame)
                if prof.vah > prof.val and prof.vah > 0:
                    self._profile = prof
                    self._vah = float(prof.vah)
                    self._val = float(prof.val)
                    self._poc = float(prof.poc)

        # 1m → 2m aggregation; the state machine runs on completed 2m bars.
        bucket = self._bucket(epoch)
        if self._pending is None or self._pending["bucket"] != bucket:
            if self._pending is not None:
                self._bars.append(self._pending["bar"])
                self._on_2m_bar(event, self._pending["bar"])
            start_ts = bucket * 120 - int(5.5 * 3600)
            start_dt = datetime.fromtimestamp(start_ts, tz=_IST)
            self._pending = {
                "bucket": bucket,
                "bar": {"start_ts": start_ts, "minute": _minute_of_day(start_dt),
                        "open": row["open"], "high": row["high"], "low": row["low"],
                        "close": row["close"], "volume": row["volume"]},
            }
        else:
            b = self._pending["bar"]
            b["high"] = max(b["high"], row["high"])
            b["low"] = min(b["low"], row["low"])
            b["close"] = row["close"]
            b["volume"] += row["volume"]

    def on_order_filled(self, event) -> None:
        """Arm the managed trade once the pending entry actually fills.

        Zero-parity across modes: in backtest/replay the fill is published
        synchronously inside ``emit_signal``; live/paper it arrives when the
        broker's fill event lands. A rejected or never-filled entry leaves
        ``_active`` unarmed, so exit management can never act on a phantom
        position.
        """
        if self._pending_order is None:
            return
        if (event.symbol != self._pending_order["symbol"]
                or event.side != self._pending_order["side"]):
            return
        filled = int(event.quantity)
        if filled <= 0:
            return
        self._active = {k: v for k, v in self._pending_order.items() if k != "symbol"}
        self._active["qty"] = filled  # exit only what actually filled
        self._pending_order = None

    def _on_2m_bar(self, event, bar: dict) -> None:
        """Process one completed 2m bar: manage exits, then look for entries."""
        bar_minute = int(bar["minute"])
        if self._active is not None:
            self._manage_exit(event, bar)
        if self._active is not None or self._pending_order is not None:
            return
        if len(self._bars) < 2 or self._profile is None:
            return
        if not (self._entry_start_min <= bar_minute < self._entry_end_min):
            return
        if self._bias not in ("UP", "DOWN"):
            return
        cur = self._bars[-1]
        prior = self._bars[-2]
        close = float(cur["close"])
        if self._bias == "UP" and float(prior["low"]) <= self._val:
            # Fake breakdown below VAL, then a bullish reversal closes back
            # above it — by default any close above VAL counts; a positive
            # reversal_margin_pct requires the close to clear VAL by that
            # fraction (filters weak/doji reversals near the level).
            clears = self._val * (1.0 + self.reversal_margin_pct)
            if close > clears and close > float(cur["open"]):
                self._enter(event, "BUY", cur, prior)
        elif self._bias == "DOWN" and float(prior["high"]) >= self._vah:
            # VAH tested and rejected — a bearish reversal closes back below.
            drops = self._vah * (1.0 - self.reversal_margin_pct)
            if close < drops and close < float(cur["open"]):
                self._enter(event, "SELL", cur, prior)

    def _enter(self, event, side: str, cur: dict, prior: dict) -> None:
        """Size + emit the entry (T1 = opposite VA level; SL beyond the signal
        bar; sizing 100% at the EMA cluster else 50%)."""
        entry = float(cur["close"])
        if side == "BUY":
            sl = min(float(cur["low"]), float(prior["low"])) - self.sl_pad
            tp = self._vah
            valid = sl < entry < tp
        else:
            sl = max(float(cur["high"]), float(prior["high"])) + self.sl_pad
            tp = self._val
            valid = tp < entry < sl
        if not valid or not (sl > 0 and tp > 0):
            return
        full = self._ema_cluster(entry)
        if self.require_cluster and not full:
            return  # only EMA-cluster entries when require_cluster is on
        qty = self._size(entry, sl, full=full)
        if qty <= 0:
            return
        self._pending_order = {"symbol": event.symbol, "side": side, "entry": entry,
                               "sl": sl, "tp": tp, "qty_total": qty,
                               "qty_left": qty, "phase": "entry",
                               "day_high": float(cur["high"]),
                               "day_low": float(cur["low"])}
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange, side=side,
            quantity=qty, price=0.0,  # MARKET
            reference_price=entry,
            sl=round(sl, 4), tp=round(tp, 4), bias=self._bias,
            sizing="full" if full else "half",
            vah=round(self._vah, 4), val=round(self._val, 4), phase="signal",
        )
        # Synchronous modes (backtest/replay): the fill lands inside
        # emit_signal, so _active is armed by on_order_filled already — a
        # rejected entry leaves it unarmed and we drop the pending so a later
        # setup can re-enter. Live keeps the pending until the fill arrives.
        if self.ctx.mode != "live" and self._active is None:
            self._pending_order = None

    def _manage_exit(self, event, bar: dict) -> None:
        """Time gates → stop → T1 partial (book 50% + breakeven) → trail."""
        act = self._active
        low, high, close = (float(bar["low"]), float(bar["high"]), float(bar["close"]))
        bar_minute = int(bar["minute"])
        side = act["side"]
        # Hard time gates first: the 11:00 close-out and the session end are
        # absolute ("never hold past the morning").
        if bar_minute >= self._hard_close_min:
            self._exit(event, "SELL" if side == "BUY" else "BUY", close,
                       reason="time_close")
            return
        if bar_minute >= self._session_end_min:
            self._exit(event, "SELL" if side == "BUY" else "BUY", close,
                       reason="session_close")
            return
        trail_idx = -1 - self.trail_back  # -2 with trail_back=1 (classic)
        # The BE-phase ratchet reads the bar `trail_back` buckets back; the
        # guard must cover the actual index (be-phase implies ≥5 bars in
        # practice, but keep the invariant self-evident).
        has_prior = len(self._bars) >= self.trail_back + 1
        if side == "BUY":
            if low <= act["sl"]:
                self._exit(event, "SELL", act["sl"], reason="stop")
                return
            if act["phase"] == "entry" and act.get("tp") is not None and high >= act["tp"]:
                self._t1(event, act["tp"])
            elif act["phase"] == "be":
                act["day_high"] = max(act["day_high"], high)
                act["day_low"] = min(act["day_low"], low)
                if self.trail_mode == "chandelier":
                    a = self._atr_now()
                    if a > 0:
                        trail = act["day_high"] - self.atr_mult * a
                        if trail > act["sl"]:
                            act["sl"] = trail
                else:
                    prior = self._bars[trail_idx] if has_prior else None
                    if prior is not None and float(prior["low"]) > act["sl"]:
                        act["sl"] = float(prior["low"])  # ratchet toward day high
        else:
            if high >= act["sl"]:
                self._exit(event, "BUY", act["sl"], reason="stop")
                return
            if act["phase"] == "entry" and act.get("tp") is not None and low <= act["tp"]:
                self._t1(event, act["tp"])
            elif act["phase"] == "be":
                act["day_high"] = max(act["day_high"], high)
                act["day_low"] = min(act["day_low"], low)
                if self.trail_mode == "chandelier":
                    a = self._atr_now()
                    if a > 0:
                        trail = act["day_low"] + self.atr_mult * a
                        if trail < act["sl"]:
                            act["sl"] = trail
                else:
                    prior = self._bars[trail_idx] if has_prior else None
                    if prior is not None and float(prior["high"]) < act["sl"]:
                        act["sl"] = float(prior["high"])  # ratchet toward day low

    def _t1(self, event, price: float) -> None:
        """T1 handling: either book ~50% (classic) or just breakeven + ride.

        ``book_partial=True``: book half at T1, breakeven on the runner (the
        video's rule). ``book_partial=False``: no fill — move the stop to
        breakeven and let the FULL position ride the trail (half the fills →
        half the per-trade statutory cost, which matters on index futures
        where STT dominates the round-trip cost). Lots are indivisible, so a
        position at/under one lot books the whole thing when partialing.
        """
        act = self._active
        qty_left = int(act["qty_left"])
        exit_side = "SELL" if act["side"] == "BUY" else "BUY"
        if self.book_partial:
            half = max(self.lot_size, (qty_left // 2 // self.lot_size) * self.lot_size)
            half = min(half, qty_left)
            if act["side"] == "BUY":
                self._day_pnl += (price - act["entry"]) * half
            else:
                self._day_pnl += (act["entry"] - price) * half
            self.emit_signal(
                symbol=event.symbol, exchange=event.exchange, side=exit_side,
                quantity=half, price=0.0,  # MARKET
                reference_price=price,
                exit_reason="partial_target", intent_price=price, partial=True,
            )
            act["qty_left"] = qty_left - half
            if act["qty_left"] <= 0:
                self._active = None
                return
        act["tp"] = None
        act["phase"] = "be"
        act["sl"] = act["entry"]  # cost-to-cost on the runner


    def _exit(self, event, side: str, price: float, *, reason: str) -> None:
        act = self._active
        qty = max(1, int(act.get("qty_left") or act["qty_total"]))
        if act["side"] == "BUY":
            self._day_pnl += (price - act["entry"]) * qty
        else:
            self._day_pnl += (act["entry"] - price) * qty
        ref = float(getattr(event, "close", price) or price)
        self.emit_signal(
            symbol=event.symbol, exchange=event.exchange, side=side,
            quantity=qty, price=0.0,  # MARKET
            reference_price=ref,
            exit_reason=reason, intent_price=price,
        )
        self._active = None
