"""Orders — rich order model that reads naturally off an Instrument."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    STOP_MARKET = "STOP_MARKET"
    COVER = "COVER"      # cover/CO order — squares a position with a stop
    BRACKET = "BRACKET"  # bracket/BO order — entry + target + stop legs


class TradeType(str, Enum):
    MIS = "MIS"
    CNC = "CNC"
    MARGIN = "MARGIN"
    MTF = "MTF"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"


OrderStatus.TERMINAL = frozenset({OrderStatus.COMPLETED.value, OrderStatus.REJECTED.value, OrderStatus.CANCELLED.value})  # type: ignore[attr-defined]


def _order_status_is_terminal(cls, status) -> bool:  # type: ignore[no-untyped-def]
    v = status.value if isinstance(status, cls) else str(status)
    return v in cls.TERMINAL  # type: ignore[attr-defined]


OrderStatus.is_terminal = classmethod(_order_status_is_terminal)  # type: ignore[attr-defined]


@dataclass
class Order:
    instrument: "Instrument"
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    trade_type: TradeType = TradeType.MIS
    price: float = 0.0
    reference_price: float = 0.0  # bar close carried from intent; 0.0 = live LTP
    trigger_price: float = 0.0
    target_price: float = 0.0   # bracket/BO target leg
    stop_loss_price: float = 0.0  # bracket/BO stop leg
    order_id: str | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_price: float = 0.0
    created_at: datetime | None = field(default=None)

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED)

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.COMPLETED

    # ------------------------------------------------- order lifecycle
    def cancel(self) -> "Order":
        """Cancel this order through the instrument's broker."""
        broker = self.instrument.broker_adapter
        if broker is None:
            raise RuntimeError(f"{self.instrument} has no broker adapter to cancel orders")
        return broker.cancel_order(self)

    def modify(self, *, price: float | None = None, quantity: int | None = None,
               order_type: OrderType | str | None = None, trigger_price: float | None = None) -> "Order":
        """Modify an open order through the instrument's broker."""
        broker = self.instrument.broker_adapter
        if broker is None:
            raise RuntimeError(f"{self.instrument} has no broker adapter to modify orders")
        return broker.modify_order(self, price=price, quantity=quantity,
                                   order_type=order_type, trigger_price=trigger_price)

    def refresh(self) -> "Order":
        """Pull the latest status/detail for this order from the broker."""
        broker = self.instrument.broker_adapter
        if broker is None:
            return self
        return broker.get_order_status(self)

    def executed_price(self) -> float:
        """Average execution price reported by the broker."""
        broker = self.instrument.broker_adapter
        if broker is None:
            return self.avg_price
        return broker.get_executed_price(self)

    def executed_price_and_time(self):
        """(price, exchange_time) reported by the broker."""
        broker = self.instrument.broker_adapter
        if broker is None:
            return self.avg_price, ""
        return broker.get_executed_price_and_time(self)

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id, "symbol": self.instrument.symbol,
            "side": self.side.value, "quantity": self.quantity,
            "order_type": self.order_type.value, "trade_type": self.trade_type.value,
            "price": self.price, "trigger_price": self.trigger_price,
            "target_price": self.target_price, "stop_loss_price": self.stop_loss_price,
            "status": self.status.value, "filled_qty": self.filled_qty,
            "avg_price": self.avg_price,
        }


# K-024: product types that are cash/delivery instruments (delivery order),
# vs derivatives (intraday margin order). An Equity left at MIS is a silent
# wrong-product-type — the caller must opt into MIS explicitly.
_CASH_DELIVERY_KINDS = frozenset({"equity", "etf", "spot"})


def _default_trade_type(instrument: "Instrument") -> TradeType:
    """Instrument-kind-aware default product type (K-024).

    Equities/ETFs/spot default to ``CNC`` (delivery); everything else
    (Future/Option/Index/others) keeps the intraday ``MIS`` default.
    """
    kind = getattr(instrument, "KIND", None)
    return TradeType.CNC if kind in _CASH_DELIVERY_KINDS else TradeType.MIS


class OrderFacade:
    """Order entry bound to an instrument: stock.order.buy(75, price=100)."""

    def __init__(self, instrument: "Instrument"):
        self.instrument = instrument

    def buy(self, quantity: int, price: float = 0.0, order_type: OrderType | str = OrderType.LIMIT,
            trade_type: TradeType | str | None = None, trigger_price: float = 0.0) -> Order:
        return self.place(OrderSide.BUY, quantity, order_type, trade_type, price, trigger_price)

    def sell(self, quantity: int, price: float = 0.0, order_type: OrderType | str = OrderType.LIMIT,
             trade_type: TradeType | str | None = None, trigger_price: float = 0.0) -> Order:
        return self.place(OrderSide.SELL, quantity, order_type, trade_type, price, trigger_price)

    def limit(self, side: OrderSide | str, quantity: int, price: float, **kw) -> Order:
        return self.place(side, quantity, OrderType.LIMIT, kw.pop("trade_type", None), price, kw.pop("trigger_price", 0.0))

    def market(self, side: OrderSide | str, quantity: int, **kw) -> Order:
        return self.place(side, quantity, OrderType.MARKET, kw.pop("trade_type", None), 0.0, 0.0)

    def stop(self, side: OrderSide | str, quantity: int, price: float, trigger_price: float, **kw) -> Order:
        return self.place(side, quantity, OrderType.STOP_LIMIT, kw.pop("trade_type", None), price, trigger_price)

    def cover(self, side: OrderSide | str, quantity: int, price: float = 0.0,
              trigger_price: float = 0.0, **kw) -> Order:
        """Cover order (CO) — entry with an attached stop, e.g. rel.order.cover("SELL", 75)."""
        return self.place(side, quantity, OrderType.COVER, kw.pop("trade_type", None), price, trigger_price)

    def bracket(self, side: OrderSide | str, quantity: int, price: float,
                target_price: float, stop_loss_price: float, **kw) -> Order:
        """Bracket order (BO) — entry + target + stop legs, e.g.
        rel.order.bracket("BUY", 75, price=2500, target_price=2600, stop_loss_price=2450)."""
        return self.place(
            side, quantity, OrderType.BRACKET, kw.pop("trade_type", None),
            price, kw.pop("trigger_price", 0.0),
            target_price=target_price, stop_loss_price=stop_loss_price,
        )

    def place(self, side, quantity, order_type=OrderType.LIMIT, trade_type: TradeType | str | None = None,
              price: float = 0.0, trigger_price: float = 0.0, **kwargs) -> Order:
        if isinstance(side, str):
            side = OrderSide(side.upper())
        if isinstance(order_type, str):
            order_type = OrderType(order_type.upper())
        if trade_type is None:
            trade_type = _default_trade_type(self.instrument)
        if isinstance(trade_type, str):
            trade_type = TradeType(trade_type.upper())
        order = Order(
            instrument=self.instrument, side=side, quantity=quantity,
            order_type=order_type, trade_type=trade_type,
            price=price, trigger_price=trigger_price, **kwargs,
        )
        broker = self.instrument.broker_adapter
        if broker is None:
            raise RuntimeError(f"{self.instrument} has no broker adapter to place orders")
        return broker.place_order(order)
