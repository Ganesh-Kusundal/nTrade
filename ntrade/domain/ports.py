"""Domain ports — the interfaces the domain layer defines and brokers implement.

Clean-architecture rule: the domain layer imports nothing from the broker
layer. Everything broker-facing is declared here as a *port*:

- ``BrokerAdapter`` — the transport boundary domain objects talk to. Brokers
  (PaperBroker, DhanBroker) implement it; instruments receive it via
  constructor injection.
- Capability machinery — ``Capability``, ``capability()``, the global registry
  and ``BrokerExtensionFacade``. This is pure plugin infrastructure: broker
  modules register broker-specific capabilities with the ``@capability``
  decorator, and ``instrument.broker.<name>()`` resolves them dynamically.

``ntrade/brokers/base.py`` and ``ntrade/brokers/capabilities.py`` re-export
these names for backward compatibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Callable

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

    def __init__(self, clock=None):
        self._subscriptions: dict[str, "Instrument"] = {}
        self._connected = False
        self._clock = clock  # optional TradingClock — zero-parity timestamp source

    # ------------------------------------------------------------ time source
    def set_clock(self, clock) -> "BrokerAdapter":
        """Inject a TradingClock so broker-produced timestamps follow replay
        time instead of the wall clock (zero-parity invariant).

        Callers may also pass ``now``/``asof`` per-call; when both are absent
        the injected clock is used, and only as a last resort the wall clock.
        """
        self._clock = clock
        return self

    def _ts(self, now=None):
        """Resolve a timestamp: explicit ``now`` > injected clock > wall clock.

        Uses ``getattr`` so brokers constructed via ``__new__`` (common in
        tests) degrade gracefully to the wall clock instead of raising.
        """
        if now is not None:
            return now
        clock = getattr(self, "_clock", None)
        if clock is not None:
            return clock.now()
        return datetime.now()

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


# =====================================================================
# Capability pattern — broker-specific extensions without polluting the
# base API. A capability is registered globally by name and declares which
# brokers support it. `instrument.broker.<name>()` resolves the capability
# dynamically; if the current broker does not support it, an AttributeError
# is raised (fail-fast, no giant if/else).
# =====================================================================

class Capability:
    def __init__(self, name: str, fn: Callable, brokers: tuple[str, ...] | None = None):
        self.name = name
        self.fn = fn
        self.brokers = brokers  # None = supported by all brokers

    def supports(self, broker_name: str) -> bool:
        return self.brokers is None or broker_name in self.brokers

    def invoke(self, instrument: "Instrument", *args, **kwargs):
        return self.fn(instrument, *args, **kwargs)


_CAPABILITIES: dict[str, Capability] = {}


def capability(name: str, brokers: tuple[str, ...] | None = None):
    """Decorator to register a broker capability, e.g.

    @capability("depth20", brokers=("dhan",))
    def _depth20(instrument, levels=20): ...
    """
    def deco(fn: Callable) -> Callable:
        _CAPABILITIES[name] = Capability(name, fn, brokers)
        return fn
    return deco


def registered_capabilities() -> dict[str, Capability]:
    return dict(_CAPABILITIES)


class BrokerExtensionFacade:
    """`instrument.broker.<capability>()` — dynamic, capability-driven access."""

    def __init__(self, instrument: "Instrument"):
        self._instrument = instrument

    def available(self) -> list[str]:
        broker = self._instrument.broker_adapter
        if broker is None:
            return []
        return [name for name, cap in _CAPABILITIES.items() if cap.supports(broker.name)]

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        cap = _CAPABILITIES.get(name)
        broker = self._instrument.broker_adapter
        if cap is None or broker is None or not cap.supports(broker.name):
            raise AttributeError(
                f"Capability {name!r} is not supported by broker {getattr(broker, 'name', None)!r} "
                f"for {self._instrument.symbol}"
            )
        return lambda *args, **kwargs: cap.invoke(self._instrument, *args, **kwargs)

    def __dir__(self):
        return sorted(set(list(super().__dir__()) + list(_CAPABILITIES.keys())))
