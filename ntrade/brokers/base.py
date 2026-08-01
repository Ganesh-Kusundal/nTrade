"""BrokerAdapter — the hidden transport boundary between domain objects and brokers.

Domain objects never talk to REST/websocket/JSON. They ask their BrokerAdapter
for quotes, history, chains and order placement. Subscriptions multiplex over a
shared transport managed by the adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from ntrade.domain.market.candles import CandleSeries
from ntrade.domain.market.depth import MarketDepth
from ntrade.domain.market.quote import Quote, Tick
from ntrade.domain.market.stream import SubscriptionState
from ntrade.domain.orders.book import OrderBook, TradeBook

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument
    from ntrade.domain.orders.order import Order


class BrokerAdapter(ABC):
    """Base class for all broker implementations. Extend per broker."""

    name = "base"

    def __init__(self):
        self._subscriptions: dict[str, "Instrument"] = {}
        self._connected = False

    # ------------------------------------------------------------ connection
    @abstractmethod
    def connect(self) -> "BrokerAdapter":
        """Establish the broker session (auth, instrument file, etc.)."""

    def disconnect(self) -> None:
        self._connected = False
        for inst in list(self._subscriptions.values()):
            inst._stream.notify_disconnect()
        self._subscriptions.clear()

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------ market data
    @abstractmethod
    def get_quote(self, instrument: "Instrument", **kwargs) -> Quote: ...

    def get_depth(self, instrument: "Instrument", **kwargs) -> MarketDepth | None:
        """Base returns no depth; brokers that support it override."""
        return None

    @abstractmethod
    def get_historical(
        self,
        instrument: "Instrument",
        timeframe: str = "5m",
        days: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> CandleSeries: ...

    def get_option_chain(self, underlying: "Instrument", expiry: int = 0, num_strikes: int = 10, **kwargs):
        raise NotImplementedError(f"{type(self).__name__} does not support option chains")

    # ------------------------------------------------------------ orders
    @abstractmethod
    def place_order(self, order: "Order") -> "Order": ...

    # ------------------------------------------------- order lifecycle (optional)
    def cancel_order(self, order: "Order") -> "Order":
        """Cancel a placed order; returns the order with updated status."""
        raise NotImplementedError(f"{type(self).__name__} does not support order cancellation")

    def modify_order(self, order: "Order", *, price=None, quantity=None, order_type=None, trigger_price=None) -> "Order":
        """Modify an open order; returns the order with updated fields."""
        raise NotImplementedError(f"{type(self).__name__} does not support order modification")

    def get_order_status(self, order: "Order") -> "Order":
        """Refresh an order's status in place; returns the order."""
        return order

    def get_order_detail(self, order_id: str) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not expose order details")

    def get_executed_price(self, order: "Order") -> float:
        """Average execution price of a completed order (0 when unknown)."""
        return float(getattr(order, "avg_price", 0.0) or 0.0)

    def get_executed_price_and_time(self, order: "Order"):
        """(price, exchange_time) for a completed order."""
        return float(getattr(order, "avg_price", 0.0) or 0.0), ""

    def get_instrument_metadata(self, instrument: "Instrument") -> dict:
        """Broker-known metadata for an instrument (tick size, lot size, freeze
        qty, circuit limits...). Default: none — brokers that know more override."""
        return {}

    def get_orderbook(self) -> OrderBook:
        raise NotImplementedError(f"{type(self).__name__} does not expose an order book")

    def get_trade_book(self) -> TradeBook:
        raise NotImplementedError(f"{type(self).__name__} does not expose a trade book")

    def order_report(self):
        raise NotImplementedError(f"{type(self).__name__} does not expose an order report")

    # ------------------------------------------------------------ portfolio
    def get_live_pnl(self) -> float:
        """Realised + unrealised P&L reported by the broker (0 when unknown)."""
        return 0.0

    def get_balance(self) -> float:
        raise NotImplementedError(f"{type(self).__name__} does not expose balance")

    def get_positions(self):
        raise NotImplementedError(f"{type(self).__name__} does not expose positions")

    def get_holdings(self):
        raise NotImplementedError(f"{type(self).__name__} does not expose holdings")

    # ------------------------------------------------------------ streaming
    def subscribe(self, instrument: "Instrument") -> None:
        """Register an instrument for live streaming (multiplexed transport)."""
        self._subscriptions[instrument.symbol] = instrument
        instrument._stream.state = SubscriptionState.SUBSCRIBED

    def unsubscribe(self, instrument: "Instrument") -> None:
        self._subscriptions.pop(instrument.symbol, None)
        instrument._stream.state = SubscriptionState.NOT_SUBSCRIBED

    def _dispatch_tick(self, instrument: "Instrument", tick: Tick) -> None:
        instrument._stream.ingest_tick(tick)
