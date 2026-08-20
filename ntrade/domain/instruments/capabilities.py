"""Instrument capabilities — decomposed capability groups for the Instrument root.

Instrument is a composition root.  Instead of exposing 60+ methods directly, it
delegates to capability objects, each grouping a coherent set of behaviors:

    instrument.market.ltp()         — read-only market data view
    instrument.stream.subscribe()   — live streaming subscription
    instrument.analytics.rsi()      — indicator computation & pattern detection
    instrument.derivatives.option_chain()  — options analytics

Capabilities are stateless views over the Instrument's internal state.  All
mutable state lives on the Instrument itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ntrade.domain.constants import QUOTE_MAX_AGE_S

if TYPE_CHECKING:
    import pandas as pd

    from ntrade.domain.instruments.chain import OptionChain
    from ntrade.domain.instruments.protocols import InstrumentProtocol as Instrument
    from ntrade.domain.market.depth import MarketDepth
    from ntrade.domain.market.history import HistoricalSeries
    from ntrade.domain.market.quote import Quote, Tick


# =====================================================================
# MarketCapability
# =====================================================================

class MarketCapability:
    """Read-only market data view.  Mode-independent (live/paper/replay).

    All data is read from the instrument's internal ``_quote``, ``_depth`` and
    ``_history`` attributes — no copies, no network calls unless explicitly
    requested (``refresh``).
    """

    def __init__(self, instrument: "Instrument"):
        self._inst = instrument

    # ---- core market data --------------------------------------------------
    def quote(self) -> "Quote":
        return self._inst._quote

    def history(self) -> "HistoricalSeries":
        return self._inst._history

    def candles(self) -> "pd.DataFrame":
        return self._inst._history.df

    def depth(self) -> "MarketDepth":
        return self._inst._depth

    # ---- scalar accessors ---------------------------------------------------
    def ltp(self) -> float:
        return self._inst._quote.ltp

    def bid(self) -> float:
        return self._inst._quote.bid

    def ask(self) -> float:
        return self._inst._quote.ask

    def volume(self) -> int:
        return self._inst._quote.volume

    def oi(self) -> int:
        return self._inst._quote.oi

    def vwap(self) -> float:
        return self._inst._quote.vwap

    def prev_close(self) -> float:
        return self._inst._quote.prev_close

    def spread(self) -> float:
        return self._inst._quote.spread()

    def mid_price(self) -> float:
        return self._inst._quote.mid_price()

    def is_stale(self, max_age_seconds: float = QUOTE_MAX_AGE_S, *, now=None) -> bool:
        return self._inst._quote.is_stale(max_age_seconds, now=now)

    # ---- lifecycle ----------------------------------------------------------
    def refresh(self) -> "Instrument":
        """Pull the latest quote (+depth) from the broker."""
        return self._inst.refresh()

    # ---- derived analytics --------------------------------------------------
    def imbalance(self) -> float:
        return self._inst._depth.bid_ask_imbalance()


# =====================================================================
# StreamCapability
# =====================================================================

class StreamCapability:
    """Live streaming subscription management.

    Delegates to the instrument's internal ``LiveStream`` (``_stream``).
    """

    def __init__(self, instrument: "Instrument"):
        self._inst = instrument

    def subscribe(self) -> "StreamCapability":
        self._inst._stream.subscribe()
        return self

    def unsubscribe(self) -> "StreamCapability":
        self._inst._stream.unsubscribe()
        return self

    def on_tick(self, cb: Any) -> "StreamCapability":
        self._inst._stream.on_tick(cb)
        return self

    def on_quote(self, cb: Any) -> "StreamCapability":
        self._inst._stream.on_quote(cb)
        return self

    def on_trade(self, cb: Any) -> "StreamCapability":
        self._inst._stream.on_trade(cb)
        return self

    def on_depth(self, cb: Any) -> "StreamCapability":
        self._inst._stream.on_depth(cb)
        return self

    def on_disconnect(self, cb: Any) -> "StreamCapability":
        self._inst._stream.on_disconnect(cb)
        return self

    def ticks(self, limit: int | None = None) -> list:
        return self._inst._stream.ticks(limit=limit)

    @property
    def is_live(self) -> bool:
        return self._inst._stream.is_live

    @property
    def last_tick(self) -> "Tick | None":
        return self._inst._stream.last_tick


# =====================================================================
# AnalyticsCapability
# =====================================================================

class AnalyticsCapability:
    """Indicator computation and pattern detection over cached history."""

    def __init__(self, instrument: "Instrument"):
        self._inst = instrument

    # ---- scalar indicators --------------------------------------------------
    def rsi(self, period: int = 14) -> float:
        return self._inst._indicators.get(f"rsi_{period}", float("nan"))

    def atr(self, period: int = 14) -> float:
        return self._inst._indicators.get(f"atr_{period}", float("nan"))

    # ---- series analytics ---------------------------------------------------
    def supertrend(self, atr_period: int = 10, multiplier: float = 3.0):
        from ntrade.domain.analytics.indicators import supertrend as _st
        df = self._inst._history.df
        if df.empty:
            return None
        st = _st(df, atr_period, multiplier)
        return st[f"STX_{atr_period}_{multiplier}"].iloc[-1]

    def heikin_ashi(self):
        from ntrade.domain.analytics.indicators import heikin_ashi as _ha
        return _ha(self._inst._history.df)

    def renko(self, box_size: float = 7.0):
        from ntrade.domain.analytics.indicators import renko_bricks as _rb
        return _rb(self._inst._history.df, box_size)

    def statistics(self) -> dict:
        df = self._inst._history.df
        if df.empty or "close" not in df:
            return {}
        closes = df["close"].astype(float)
        returns = closes.pct_change().dropna()
        first = closes.iloc[0]
        total_return = (closes.iloc[-1] / first - 1) * 100 if first else 0.0
        return {
            "last": float(closes.iloc[-1]),
            "mean": float(closes.mean()),
            "std": float(closes.std()),
            "min": float(closes.min()),
            "max": float(closes.max()),
            "total_return_pct": round(total_return, 4),
            "volatility_pct": round(float(returns.std()) * 100, 4),
        }

    # ---- bulk computation ---------------------------------------------------
    def compute(self, **params: Any) -> "AnalyticsCapability":
        from ntrade.domain.analytics.indicators import compute_bundle
        self._inst._indicators.update(compute_bundle(self._inst._history.df, **params))
        return self

    # ---- pattern detection --------------------------------------------------
    def detect_breakout(self, lookback: int = 20) -> bool:
        df = self._inst._history.df
        if df.empty or len(df) < lookback or "close" not in df:
            return False
        recent_high = df["close"].iloc[-(lookback + 1):-1].max()
        return bool(df["close"].iloc[-1] > recent_high)

    def detect_imbalance(self) -> float:
        return self._inst._depth.bid_ask_imbalance()

    # ---- accessors ----------------------------------------------------------
    @property
    def indicators(self) -> dict[str, float]:
        return self._inst._indicators


# =====================================================================
# DerivativesCapability
# =====================================================================

class DerivativesCapability:
    """Option chain and derivatives analytics."""

    def __init__(self, instrument: "Instrument"):
        self._inst = instrument

    def option_chain(self, expiry: int = 0, num_strikes: int = 10, **kw: Any) -> "OptionChain":
        from ntrade.domain.instruments.chain import OptionChain
        return OptionChain.fetch(self._inst, expiry=expiry, num_strikes=num_strikes, **kw)
