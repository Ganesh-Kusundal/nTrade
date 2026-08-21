"""Order-type strategy table — one strategy per OrderType.

Shot-gun surgery elimination: adding an OrderType should touch only this file.
Maps each ``OrderType`` to its broker routing and metadata. Call sites should
delegate to ``strategy_for`` / ``route_for_broker`` instead of branching on
``order_type.value == "BRACKET"`` etc.

TODO: refactor call sites to use this table:
  - ntrade/brokers/dhan.py:293  (BRACKET -> super_order dispatch)
  - ntrade/brokers/paper.py:115-138 (fill-price branching)
  - ntrade/execution/simulator.py:80-94 (MARKET vs LIMIT fill)
  - ntrade/execution/broker_executor.py:134 (order placement)

Usage:
    from ntrade.execution.order_types import strategy_for, route_for_broker, ROUTE_MAP
    route = route_for_broker(order.order_type)  # "super_order" | "order_placement"
"""
from __future__ import annotations

from ntrade.domain.orders.order import OrderType

# Broker routing: which Dhan/transport API to use.
# Only BRACKET requires the dedicated super-order endpoint (entry + target + stop);
# all others go through the regular order_placement path (including COVER which
# Dhan currently handles as a regular order — extend here if it needs super_order).
ROUTE_MAP: dict[OrderType, str] = {
    OrderType.MARKET: "order_placement",
    OrderType.LIMIT: "order_placement",
    OrderType.STOP_LIMIT: "order_placement",
    OrderType.STOP_MARKET: "order_placement",
    OrderType.COVER: "order_placement",
    OrderType.BRACKET: "super_order",
}

# Rich strategy table — one entry per OrderType. ``route`` mirrors ROUTE_MAP;
# additional keys (e.g. requires_trigger, legs) can be added without touching
# call sites — they just read strategy_for(type)[key].
_STRATEGIES: dict[OrderType, dict] = {
    OrderType.MARKET: {"route": "order_placement", "requires_price": False, "requires_trigger": False},
    OrderType.LIMIT: {"route": "order_placement", "requires_price": True, "requires_trigger": False},
    OrderType.STOP_LIMIT: {"route": "order_placement", "requires_price": True, "requires_trigger": True},
    OrderType.STOP_MARKET: {"route": "order_placement", "requires_price": False, "requires_trigger": True},
    OrderType.COVER: {"route": "order_placement", "requires_price": False, "requires_trigger": True, "legs": 2},
    OrderType.BRACKET: {"route": "super_order", "requires_price": True, "requires_trigger": False, "legs": 3},
}


def _coerce(order_type: OrderType | str) -> OrderType:
    if isinstance(order_type, OrderType):
        return order_type
    return OrderType(str(order_type).upper())


def strategy_for(order_type: OrderType | str) -> dict:
    """Return the strategy dict for an order type (string or enum)."""
    ot = _coerce(order_type)
    try:
        return dict(_STRATEGIES[ot])
    except KeyError:
        raise KeyError(f"unknown OrderType: {ot!r}") from None


def route_for_broker(order_type: OrderType | str) -> str:
    """Return broker routing string: 'super_order' vs 'order_placement'."""
    ot = _coerce(order_type)
    try:
        return ROUTE_MAP[ot]
    except KeyError:
        raise KeyError(f"unknown OrderType: {ot!r}") from None
