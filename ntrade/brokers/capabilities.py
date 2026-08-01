"""Capability pattern — broker-specific extensions without polluting the base API.

A capability is registered globally by name and declares which brokers support it.
`instrument.broker.<name>()` resolves the capability dynamically; if the current
broker does not support it, an AttributeError is raised (fail-fast, no giant if/else).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument


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
