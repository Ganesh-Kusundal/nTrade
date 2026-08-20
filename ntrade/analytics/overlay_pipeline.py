"""OverlayPipeline — single backend source of truth for chart overlays.

One call turns a candle series into every overlay the UI draws: VWAP ±σ
bands, session-anchored volume profile (POC/VAH/VAL), absorption markers,
optional range bars, and — when a strategy id is requested — the strategy's
trade/level markers produced by the *real* strategy classes (no fork).

The strategy path replays `CandleClosedEvent`s through an actual `Strategy`
instance (the same classes the kernel uses for live/backtest/replay), so
chart markers can never diverge from paper/live fills. A minimal headless
context provides `ctx.now()`, `ctx.account` and `ctx.instrument`:
- `now()` returns the current bar's timestamp (only used to stamp emitted
  signals; session gates use `event.ts` directly).
- `account.balance` is a fixed notional so entry sizing is deterministic
  offline (matches risk-budget math; callers may pass `balance`).
- `instrument()` returns None so the optional live-only depth gate is skipped
  (zero-parity: backtest/replay/paper also skip it when no book exists).

Source of truth: this is the ONLY place the API computes overlays.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ntrade.domain.analytics.indicators import vwap, vwap_bands
from ntrade.domain.analytics.order_flow import detect_absorptions
from ntrade.domain.analytics.range_bars import build_range_bars
from ntrade.domain.analytics.volume_profile import (
    VolumeProfile, build_volume_profile)
from ntrade.domain.market_hours import is_market_open, session_open
from ntrade.engines.strategies import _strategy_classes
from ntrade.events.market import CandleClosedEvent

log = logging.getLogger("ntrade.overlay")

_IST = ZoneInfo("Asia/Kolkata")


# --------------------------------------------------------------------------- DTOs
@dataclass
class OverlayDTO:
    """JSON-serializable overlay payload for one symbol/interval."""
    symbol: str
    exchange: str
    interval: str
    candles: list[dict] = field(default_factory=list)
    vwap: list[dict] | None = None
    vwap_upper: list[dict] | None = None
    vwap_lower: list[dict] | None = None
    volume_profile: dict | None = None
    absorptions: list[dict] | None = None
    range_bars: list[dict] | None = None
    strategy: dict | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "interval": self.interval,
            "candles": self.candles,
            "overlays": {
                "vwap": self.vwap,
                "vwap_upper": self.vwap_upper,
                "vwap_lower": self.vwap_lower,
                "volume_profile": self.volume_profile,
                "absorptions": self.absorptions,
                "range_bars": self.range_bars,
            },
            "strategy": self.strategy,
        }


# --------------------------------------------------------------------- helpers
def _candles_to_frame(candles: list[dict]) -> pd.DataFrame:
    """Normalize the wire candle dicts into a frame.

    Input candles are `{time,open,high,low,close,volume}` with `time` as epoch
    seconds. Returns a frame sorted by `time`, deduplicated.
    """
    if not candles:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame([
        {
            "time": int(c["time"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c.get("volume", 0)),
        }
        for c in candles
    ])
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def _session_anchor(df: pd.DataFrame, exchange: str) -> pd.DataFrame:
    """Restrict a frame to the current IST session (09:15 NSE / 09:00 MCX).

    Volume profile + VWAP are session-anchored; the UI's `sessionProfileCandles`
    does the same cut. Uses the frame's own latest bar to decide "today".
    """
    if df.empty:
        return df
    latest = datetime.fromtimestamp(int(df["time"].iloc[-1]), tz=_IST)
    if not is_market_open(exchange, latest):
        return df  # out of hours: show whatever we have
    open_t = session_open(exchange)
    op = latest.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
    start = int(op.timestamp())
    return df[df["time"] >= start].reset_index(drop=True)


# ------------------------------------------------------------- build overlays
def _build_vwap(df: pd.DataFrame, session: pd.DataFrame) -> dict | None:
    if session.empty:
        return None
    from ntrade.domain.analytics.indicators import vwap as _vwap_series
    ser = _vwap_series(session)
    n = len(df)
    out = [{"time": int(df["time"].iloc[i]),
            "value": round(float(ser.iloc[i]), 4) if i < len(ser) else None}
           for i in range(n)]
    upper = lower = None
    u, l = vwap_bands(session)
    import pandas as _pd
    if _pd.notna(u) and _pd.notna(l):
        upper = [{"time": int(df["time"].iloc[i]),
                  "value": round(float(u), 4) if i == n - 1 else None}
                 for i in range(n)]
        lower = [{"time": int(df["time"].iloc[i]),
                  "value": round(float(l), 4) if i == n - 1 else None}
                 for i in range(n)]
    return {"vwap": out, "upper": upper, "lower": lower}


def _build_volume_profile(df: pd.DataFrame, session: pd.DataFrame) -> dict | None:
    if session.empty:
        return None
    vp: VolumeProfile = build_volume_profile(session)
    if not vp.levels:
        return None
    return {
        "step": round(float(vp.step), 4),
        "poc": round(float(vp.poc), 4),
        "vah": round(float(vp.vah), 4),
        "val": round(float(vp.val), 4),
        "levels": [
            {"price": round(float(lvl.price), 4), "volume": round(float(lvl.volume), 4)}
            for lvl in vp.levels
        ],
    }


def _build_absorptions(df: pd.DataFrame) -> list[dict] | None:
    if df.empty:
        return None
    bars = detect_absorptions(df)
    if not bars:
        return []
    out = []
    for a in bars:
        out.append({
            "index": a.bar_index,
            "time": int(df["time"].iloc[min(a.bar_index, len(df) - 1)]),
            "price": round(float(a.price), 4),
            "volume": round(float(a.volume), 4),
            "side": a.side,
            "strength": round(float(a.strength), 4),
        })
    return out


def _build_range_bars(df: pd.DataFrame, tick_size: float | None,
                      range_size: float | None) -> list[dict] | None:
    if df.empty:
        return None
    # build_range_bars expects a `timestamp` column; the candle frame uses `time`.
    src = df.rename(columns={"time": "timestamp"})
    rb = build_range_bars(src, range_size=range_size, tick_size=tick_size)
    if rb.empty:
        return []
    out = []
    for _, r in rb.iterrows():
        out.append({
            "time": int(r["timestamp"]),
            "open": round(float(r["open"]), 4),
            "high": round(float(r["high"]), 4),
            "low": round(float(r["low"]), 4),
            "close": round(float(r["close"]), 4),
            "volume": round(float(r["volume"]), 4),
            "is_complete": bool(r["is_complete"]),
        })
    return out


# --------------------------------------------------------------- strategy run
class _DummyBus:
    def subscribe(self, *a, **k):
        pass

    def publish(self, *a, **k):
        pass


class _FlatPortfolio:
    """Reports zero open positions for headless EmaCross replay."""

    def position(self, symbol: str):
        return None


class _IndicatorStub:
    """Lazy EMA bundle for headless EmaCrossStrategy replay.

    ``_indicators`` mirrors the dict the real IndicatorEngine projects onto an
    instrument; we compute just the EMAs on demand from the replayed rows.
    """

    def __init__(self, rows: list[dict]):
        self._rows = rows

    @property
    def _indicators(self) -> dict:
        if not self._rows:
            return {}
        frame = pd.DataFrame(self._rows)
        out: dict[str, float] = {}
        for period in (9, 21, 10, 20):
            ser = frame["close"].astype(float).ewm(span=period, min_periods=period).mean()
            out[f"ema_{period}"] = float(ser.iloc[-1]) if len(ser) >= period else None
        return out


class _HeadlessContext:
    """Minimal ctx for a faithful offline strategy replay."""

    def __init__(self, balance: float = 100_000.0):
        self._balance = float(balance)
        self.mode = "replay"
        self._ts = None
        self.bus = _DummyBus()
        self._rows_by_symbol: dict[str, list[dict]] = {}
        # `self.ctx.account.balance` is read by strategy sizing math.
        self.balance = self._balance

    def now(self) -> datetime:
        return self._ts

    def instrument(self, symbol: str):
        # EmaCrossStrategy reads _indicators (EMA bundle) from a registered
        # instrument. Provide a stub that computes the EMAs on demand from the
        # candle rows we are replaying — the same math IndicatorEngine feeds in
        # live/backtest, so the EMA cross path stays faithful (no fork).
        return _IndicatorStub(self._rows_by_symbol.setdefault(symbol, []))

    @property
    def account(self):
        return self

    @property
    def portfolio(self):
        # EmaCrossStrategy reads ctx.portfolio.position(); the headless replay
        # never opens a managed position, so report flat everywhere.
        return _FlatPortfolio()


class _SignalBus:
    """Routes SignalGeneratedEvent to a callback so we capture markers."""

    def __init__(self, on_signal):
        self._on_signal = on_signal

    def subscribe(self, *a, **k):
        pass

    def publish(self, ev):
        if ev.__class__.__name__ == "SignalGeneratedEvent":
            self._on_signal(ev)


def _run_strategy(candles: list[dict], strategy_id: str, params: dict | None,
                  symbol: str, exchange: str, timeframe: str, balance: float,
                  df: pd.DataFrame) -> dict | None:
    """Replay candles through the real strategy class; emit markers.

    Returns a dict shaped like the UI StrategyOverlay: signals, levels, phase,
    bias.
    """
    if strategy_id not in _strategy_classes:
        log.warning("unknown strategy id %r — skipped", strategy_id)
        return None
    cls = _strategy_classes[strategy_id]
    inst = cls(**(params or {}))
    ctx = _HeadlessContext(balance=balance)

    signals: list[dict] = []

    def _on_signal(ev) -> None:
        md = dict(ev.metadata or {})
        signals.append({
            "symbol": ev.symbol,
            "side": ev.side,
            "quantity": ev.quantity,
            "phase": md.get("phase"),
            "exit_reason": md.get("exit_reason"),
            "intent_price": md.get("intent_price"),
            "sl": md.get("sl"),
            "tp": md.get("tp"),
            "reference_price": md.get("reference_price"),
        })

    ctx.bus = _SignalBus(_on_signal)
    inst.ctx = ctx

    for c in candles:
        ts = datetime.fromtimestamp(int(c["time"]), tz=_IST)
        ctx._ts = ts
        ctx._rows_by_symbol.setdefault(symbol, []).append({
            "timestamp": ts,
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
            "volume": float(c.get("volume", 0)),
        })
        ev = CandleClosedEvent(
            symbol=symbol, exchange=exchange, timeframe=timeframe,
            open=float(c["open"]), high=float(c["high"]), low=float(c["low"]),
            close=float(c["close"]), volume=float(c.get("volume", 0)),
            ts=ts,
        )
        inst.on_candle_closed(ev)

    out: dict[str, Any] = {"id": strategy_id, "signals": signals}
    if hasattr(inst, "phase"):
        try:
            out["phase"] = inst.phase
        except Exception:  # noqa: BLE001
            pass
    if hasattr(inst, "bias"):
        try:
            out["bias"] = inst.bias
        except Exception:  # noqa: BLE001
            pass
    profile = getattr(inst, "_profile", None)
    if profile is not None:
        out["levels"] = [{
            "date": datetime.fromtimestamp(int(candles[-1]["time"]), tz=_IST).strftime("%Y-%m-%d")
            if candles else "",
            "vah": round(float(profile.vah), 4),
            "val": round(float(profile.val), 4),
            "poc": round(float(profile.poc), 4),
        }]
    return out


# -------------------------------------------------------------------- pipeline
def replay_strategy(
    candles: list[dict],
    *,
    strategy_id: str,
    symbol: str,
    exchange: str,
    timeframe: str = "1m",
    strategy_params: dict | None = None,
    balance: float = 100_000.0,
) -> dict | None:
    """Headless bar-replay adapter — the single strategy path for chart overlays,
    paper, and backtest parity checks.

    Replays `CandleClosedEvent`s through the registered strategy class and
    returns its markers (signals / levels / phase / bias). Callers that also
    want indicators should use `build_overlays(..., strategy_id=...)`.
    """
    df = _candles_to_frame(candles)
    return _run_strategy(candles, strategy_id, strategy_params, symbol,
                         exchange, timeframe, balance, df)


def build_overlays(
    candles: list[dict],
    *,
    symbol: str,
    exchange: str,
    interval: str,
    strategy_id: str | None = None,
    strategy_params: dict | None = None,
    tick_size: float | None = None,
    range_size: float | None = None,
    balance: float = 100_000.0,
    include_range_bars: bool = False,
) -> OverlayDTO:
    """Compute every overlay for a candle series.

    Args:
        candles: wire candles `{time,open,high,low,close,volume}`.
        strategy_id: optional registered strategy; when set, markers are
            produced by replaying the real strategy class.
        include_range_bars: also return range bars (used by Valentini; the
            chart draws the time-based frame, range bars feed strategy state).
    """
    df = _candles_to_frame(candles)
    session = _session_anchor(df, exchange)

    dto = OverlayDTO(
        symbol=symbol, exchange=exchange, interval=interval,
        candles=[{
            "time": int(c["time"]), "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
            "volume": float(c.get("volume", 0)),
        } for c in df.to_dict("records")],
    )

    v = _build_vwap(df, session)
    if v is not None:
        dto.vwap = v["vwap"]
        dto.vwap_upper = v["upper"]
        dto.vwap_lower = v["lower"]
    else:
        dto.vwap = None

    dto.volume_profile = _build_volume_profile(df, session)
    dto.absorptions = _build_absorptions(df)
    if include_range_bars:
        dto.range_bars = _build_range_bars(df, tick_size, range_size)

    if strategy_id:
        dto.strategy = _run_strategy(
            candles, strategy_id, strategy_params, symbol, exchange,
            interval, balance, df)

    return dto


def build_chart(candles: list[dict], *, symbol: str, exchange: str,
                interval: str, **kw) -> dict:
    """Thin wrapper returning the JSON dict the API serves."""
    return build_overlays(candles, symbol=symbol, exchange=exchange,
                          interval=interval, **kw).to_dict()
