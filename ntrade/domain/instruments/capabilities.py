"""Instrument capabilities — decomposed capability groups for the Instrument root.

Instrument is a composition root.  Instead of exposing 60+ methods directly, it
delegates to six capability objects, each grouping a coherent set of behaviors:

    instrument.market.ltp()         — read-only market data view
    instrument.trade.buy().place()  — order entry (fluent builder)
    instrument.stream.subscribe()   — live streaming subscription
    instrument.analytics.rsi()      — indicator computation & pattern detection
    instrument.derivatives.option_chain()  — options analytics
    instrument.extension(Cls)       — provider-specific extensions

Capabilities are stateless views over the Instrument's internal state.  All
mutable state lives on the Instrument itself.
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

    from ntrade.domain.instruments.base import Instrument
    from ntrade.domain.instruments.chain import OptionChain
    from ntrade.domain.market.depth import MarketDepth
    from ntrade.domain.market.history import HistoricalSeries
    from ntrade.domain.market.quote import Quote, Tick
    from ntrade.domain.orders.order import Order, OrderFacade


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

    def is_stale(self, max_age_seconds: float = 5.0, *, now=None) -> bool:
        return self._inst._quote.is_stale(max_age_seconds, now=now)

    # ---- lifecycle ----------------------------------------------------------
    def refresh(self) -> "Instrument":
        """Pull the latest quote (+depth) from the broker."""
        return self._inst.refresh()

    # ---- derived analytics --------------------------------------------------
    def imbalance(self) -> float:
        return self._inst._depth.bid_ask_imbalance()


# =====================================================================
# TradeCapability  +  OrderBuilder
# =====================================================================

class OrderBuilder:
    """Fluent order construction: ``tcs.trade.buy().market().quantity(100).place()``.

    Created per-call, mutable, single-use.  Internally delegates to the
    existing ``OrderFacade`` → ``BrokerAdapter.place_order()`` pipeline.
    """

    def __init__(self, instrument: "Instrument", side: str):
        self._instrument = instrument
        self._side = side.upper()
        self._quantity: int = 0
        self._price: float = 0.0
        self._trigger_price: float = 0.0
        self._order_type: str = "LIMIT"
        self._trade_type: str = "MIS"
        self._product: str = "MIS"
        self._target_price: float = 0.0
        self._stop_loss_price: float = 0.0

    # ---- price type ---------------------------------------------------------
    def market(self) -> "OrderBuilder":
        self._order_type = "MARKET"
        self._price = 0.0
        return self

    def limit(self, price: float) -> "OrderBuilder":
        self._order_type = "LIMIT"
        self._price = price
        return self

    def stop(self, trigger: float) -> "OrderBuilder":
        self._order_type = "STOP_LIMIT"
        self._trigger_price = trigger
        return self

    # ---- order fields -------------------------------------------------------
    def quantity(self, qty: int) -> "OrderBuilder":
        self._quantity = qty
        return self

    def price(self, price: float) -> "OrderBuilder":
        self._price = price
        return self

    def trigger_price(self, price: float) -> "OrderBuilder":
        self._trigger_price = price
        return self

    def product(self, product: str) -> "OrderBuilder":
        """Set the product type: ``MIS`` | ``CNC`` | ``NRML``."""
        self._product = product
        self._trade_type = product
        return self

    def target(self, price: float) -> "OrderBuilder":
        """Bracket-order target leg."""
        self._target_price = price
        return self

    def stop_loss(self, price: float) -> "OrderBuilder":
        """Bracket-order stop-loss leg."""
        self._stop_loss_price = price
        return self

    # ---- terminal -----------------------------------------------------------
    def place(self) -> "Order":
        """Submit the order through the instrument's broker adapter."""
        facade: "OrderFacade" = self._instrument.order
        kwargs: dict[str, Any] = {}
        if self._target_price:
            kwargs["target_price"] = self._target_price
        if self._stop_loss_price:
            kwargs["stop_loss_price"] = self._stop_loss_price
        return facade.place(
            self._side,
            self._quantity,
            order_type=self._order_type,
            trade_type=self._trade_type,
            price=self._price,
            trigger_price=self._trigger_price,
            **kwargs,
        )


class TradeCapability:
    """Order entry.  Builder pattern for fluent order construction."""

    def __init__(self, instrument: "Instrument"):
        self._inst = instrument

    def buy(self) -> OrderBuilder:
        return OrderBuilder(self._inst, "BUY")

    def sell(self) -> OrderBuilder:
        return OrderBuilder(self._inst, "SELL")

    def cancel(self, order: "Order") -> "Order":
        return order.cancel()

    def modify(self, order: "Order", **kw: Any) -> "Order":
        return order.modify(**kw)


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

    def candle_stream(self) -> "pd.DataFrame":
        return self._inst._stream.live_ticks_df

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

    def detect_absorption(self, threshold: float = 2.0) -> bool:
        bb, ba = self._inst._depth.best_bid(), self._inst._depth.best_ask()
        if bb is None or ba is None or self._inst._quote.volume <= 0:
            return False
        top_qty = bb.quantity + ba.quantity
        return top_qty >= threshold * self._inst._quote.volume

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


# =====================================================================
# ExtensionCapability
# =====================================================================

class ExtensionCapability:
    """Provider-specific extensions (class-based, not string-based).

    Usage::

        tcs.extension(GTTExtension)
    """

    def __init__(self, instrument: "Instrument"):
        self._inst = instrument

    def __call__(self, extension_class: type) -> Any:
        """Resolve *extension_class* against the current broker."""
        from ntrade.brokers.capabilities import BrokerExtensionFacade
        facade = BrokerExtensionFacade(self._inst)
        name = getattr(extension_class, "name", extension_class.__name__.lower())
        return getattr(facade, name)
