"""Instrument — the abstract root of every market entity in ntrade.

Every instrument owns its own state (quote, depth, history, stream, indicators,
metadata, session) and exposes *capability* objects for market data, streaming,
analytics and derivatives.  Broker transport is hidden behind a BrokerAdapter;
broker-specific capabilities live behind ``instrument.broker``.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any, Callable

from ntrade.domain.market.depth import MarketDepth
from ntrade.domain.market.history import HistoricalSeries
from ntrade.domain.market.quote import Quote
from ntrade.domain.market.stream import LiveStream
from ntrade.domain.session import MarketState, SessionState

if TYPE_CHECKING:
    from ntrade.brokers.base import BrokerAdapter
    from ntrade.brokers.capabilities import BrokerExtensionFacade
    from ntrade.domain.instruments.capabilities import (
        AnalyticsCapability,
        DerivativesCapability,
        MarketCapability,
        StreamCapability,
    )
    from ntrade.domain.orders.order import OrderFacade


@dataclass(frozen=True)
class CorporateAction:
    """A corporate action affecting an instrument (dividend, split, bonus...)."""

    action_type: str  # "dividend" | "split" | "bonus" | "merger" | ...
    amount: float = 0.0
    ratio: str = ""  # e.g. "1:2" for a 1-for-2 split
    ex_date: date | None = None
    record_date: date | None = None
    description: str = ""


class Instrument(ABC):
    """Base class for all market objects. Subclasses specialize per asset class."""

    KIND = "instrument"
    DEFAULT_EXCHANGE = "NSE"

    def __init__(
        self,
        symbol: str,
        exchange: str | None = None,
        *,
        name: str | None = None,
        currency: str = "INR",
        tick_size: float | None = None,
        lot_size: int | None = None,
        freeze_qty: int | None = None,
        broker: "BrokerAdapter | None" = None,
        broker_factory: Callable[[], "BrokerAdapter"] | None = None,
        **metadata: Any,
    ):
        self.symbol = symbol
        self.exchange = exchange or self.DEFAULT_EXCHANGE
        self.name = name or symbol
        self.currency = currency
        self.tick_size = tick_size
        self.lot_size = lot_size
        self.freeze_qty = freeze_qty

        # --- state owned by the instrument ---------------------------------
        self._quote: Quote = Quote.empty()
        self._depth: MarketDepth = MarketDepth.empty(symbol)
        self._history: HistoricalSeries = HistoricalSeries(self)
        self._stream: LiveStream = LiveStream(self)
        self._indicators: dict[str, float] = {}
        self._signals: dict[str, Any] = {}
        self._annotations: dict[str, str] = {}
        self._tags: set[str] = set()
        self._metadata: dict[str, Any] = dict(metadata)
        self._subscription_state = "not_subscribed"
        self._corporate_actions: list[CorporateAction] = []
        self._metadata_hydrated = False
        self._last_refresh_at: datetime | None = None
        self._session = SessionState(exchange=self.exchange)
        self._market_status = MarketState.CLOSED

        # --- broker wiring (lazy so instruments can be created broker-free) ---
        self._broker: "BrokerAdapter | None" = broker
        self._broker_factory = broker_factory

    # ================================================================== broker
    @property
    def broker_adapter(self) -> "BrokerAdapter | None":
        if self._broker is None and self._broker_factory is not None:
            self._broker = self._broker_factory()
        return self._broker

    @property
    def broker(self) -> "BrokerExtensionFacade":
        """Broker-specific capabilities (e.g. nifty.broker.depth20())."""
        from ntrade.brokers.capabilities import BrokerExtensionFacade
        return BrokerExtensionFacade(self)

    @property
    def extensions(self) -> "BrokerExtensionFacade":
        return self.broker

    @property
    def order(self) -> "OrderFacade":
        """Order entry that reads naturally: stock.order.buy(75)."""
        from ntrade.domain.orders.order import OrderFacade
        return OrderFacade(self)

    # ================================================================== capabilities
    @cached_property
    def market(self) -> "MarketCapability":
        from ntrade.domain.instruments.capabilities import MarketCapability
        return MarketCapability(self)

    @property
    def history(self) -> "HistoricalSeries":
        """Canonical historical OHLCV accessor (mirrors ``market``)."""
        return self._history

    @cached_property
    def stream(self) -> "StreamCapability":
        from ntrade.domain.instruments.capabilities import StreamCapability
        return StreamCapability(self)

    @cached_property
    def analytics(self) -> "AnalyticsCapability":
        from ntrade.domain.instruments.capabilities import AnalyticsCapability
        return AnalyticsCapability(self)

    @cached_property
    def derivatives(self) -> "DerivativesCapability":
        from ntrade.domain.instruments.capabilities import DerivativesCapability
        return DerivativesCapability(self)

    # ================================================================== quote / depth
    def apply_quote(self, quote: Quote) -> "Instrument":
        """Project a market quote into this instrument (event-driven read model).

        The kernel calls this when a QuoteUpdatedEvent arrives — the instrument
        never needs to ask the broker again.
        """
        self._quote = quote
        return self

    def apply_depth(self, depth: MarketDepth) -> "Instrument":
        """Project an order-book snapshot into this instrument (read model)."""
        self._depth = depth
        return self

    # ================================================================== lifecycle
    def refresh(self, force: bool = False, *, now: datetime | None = None) -> "Instrument":
        """Pull the latest quote (+depth when supported) from the broker."""
        broker = self.broker_adapter
        if broker is None:
            return self
        if not self._metadata_hydrated:
            self.hydrate()  # hydrate tick/lot/freeze metadata once
        try:
            self._quote = broker.get_quote(self)
        except Exception:
            pass  # keep previous quote on broker failure
        depth = broker.get_depth(self)
        if depth is not None:
            self._depth = depth
        self._last_refresh_at = now if now is not None else datetime.now()
        return self

    def hydrate(self) -> "Instrument":
        """Hydrate broker-known metadata (tick size, lot size, freeze qty,
        circuit limits) from the broker adapter into this instrument's state.

        No-op when the broker exposes no instrument metadata. Runs at most once
        (refresh() also triggers it lazily on first call).
        """
        if self._metadata_hydrated:
            return self
        broker = self.broker_adapter
        if broker is not None:
            meta = broker.get_instrument_metadata(self)
            if meta.get("tick_size") is not None:
                self.tick_size = meta["tick_size"]
            if meta.get("lot_size") is not None:
                self.lot_size = meta["lot_size"]
            if meta.get("freeze_qty") is not None:
                self.freeze_qty = meta["freeze_qty"]
            if meta.get("circuit_low") is not None:
                self._quote = self._quote.with_update(circuit_low=meta["circuit_low"])
            if meta.get("circuit_high") is not None:
                self._quote = self._quote.with_update(circuit_high=meta["circuit_high"])
        self._metadata_hydrated = True
        return self

    def session(self) -> SessionState:
        return self._session

    # ================================================================== corporate actions
    def record_corporate_action(
        self, action_type: str, *,
        amount: float = 0.0, ratio: str = "",
        ex_date: date | None = None, record_date: date | None = None,
        description: str = "",
    ) -> "Instrument":
        """Record a corporate action (dividend/split/bonus/merger...)."""
        self._corporate_actions.append(CorporateAction(
            action_type=action_type, amount=amount, ratio=ratio,
            ex_date=ex_date, record_date=record_date, description=description,
        ))
        return self

    @property
    def corporate_actions(self) -> list[CorporateAction]:
        return list(self._corporate_actions)

    def clear_corporate_actions(self) -> "Instrument":
        self._corporate_actions.clear()
        return self

    # ================================================================== signals
    def set_signal(self, name: str, value: Any) -> "Instrument":
        """Record a strategy/pattern signal on this instrument."""
        self._signals[name] = value
        return self

    def get_signal(self, name: str, default: Any = None):
        return self._signals.get(name, default)

    @property
    def signals(self) -> dict[str, Any]:
        return dict(self._signals)

    @property
    def last_refresh_at(self) -> datetime | None:
        return self._last_refresh_at

    def snapshot(self) -> dict:
        return {
            "symbol": self.symbol, "exchange": self.exchange, "kind": self.KIND,
            "name": self.name, "quote": self._quote.as_dict(),
            "is_live": self._stream.is_live,
            "market_status": self._market_status.value,
            "session": self._session.state.value,
            "last_refresh_at": self._last_refresh_at.isoformat() if self._last_refresh_at else None,
        }

    # ================================================================== identity
    def clone(self) -> "Instrument":
        """Return a fresh, independent copy with the same identity + broker."""
        return type(self)(
            symbol=self.symbol, exchange=self.exchange, name=self.name,
            currency=self.currency, tick_size=self.tick_size, lot_size=self.lot_size,
            freeze_qty=self.freeze_qty, broker=self._broker, **self._metadata,
        )

    @property
    def market_status(self) -> MarketState:
        return self._market_status

    def set_market_status(self, state: MarketState) -> "Instrument":
        self._market_status = state
        self._session.enter(state)
        return self

    def serialize(self) -> dict:
        return self.snapshot()

    def tag(self, tag: str) -> "Instrument":
        self._tags.add(tag)
        return self

    def annotate(self, key: str, value: str) -> "Instrument":
        self._annotations[key] = value
        return self

    @property
    def tags(self) -> set[str]:
        return set(self._tags)

    @property
    def annotations(self) -> dict[str, str]:
        return dict(self._annotations)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(symbol={self.symbol!r}, exchange={self.exchange!r}, ltp={self._quote.ltp})"

    def __str__(self) -> str:
        return f"{self.symbol} ({self.exchange})"
