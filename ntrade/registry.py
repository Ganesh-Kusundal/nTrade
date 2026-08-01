"""Registry — Flyweight + Registry patterns.

SymbolMaster caches instrument instances so the same symbol always resolves to
the same object (shared metadata, subscriptions, cache state).
BrokerRegistry maps broker names to factory callables.
"""

from __future__ import annotations

from typing import Callable, Type

from ntrade.domain.instruments.base import Instrument


class SymbolMaster:
    """Flyweight: shared instrument instances keyed by (kind, symbol, exchange)."""

    def __init__(self):
        self._cache: dict[tuple, Instrument] = {}

    def get(
        self,
        cls: Type[Instrument],
        symbol: str,
        exchange: str | None = None,
        *,
        force_new: bool = False,
        **specs,
    ) -> Instrument:
        kind = getattr(cls, "KIND", cls.__name__.lower())
        key = (kind, symbol, exchange or getattr(cls, "DEFAULT_EXCHANGE", "NSE"))
        if force_new or key not in self._cache:
            inst = cls(symbol, exchange=exchange, **specs)
            self._cache[key] = inst
        return self._cache[key]

    def invalidate(self, symbol: str, exchange: str | None = None) -> None:
        for key in list(self._cache.keys()):
            if key[1] == symbol and (exchange is None or key[2] == exchange):
                del self._cache[key]

    def clear(self) -> None:
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class BrokerRegistry:
    """Registry of broker factories: BrokerRegistry.get("dhan") -> DhanBroker."""

    _factories: dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str, factory: Callable) -> None:
        cls._factories[name] = factory

    @classmethod
    def get(cls, name: str, **kwargs):
        _ensure_default_brokers()
        if name not in cls._factories:
            raise KeyError(f"No broker registered under {name!r}; available: {sorted(cls._factories)}")
        return cls._factories[name](**kwargs)

    @classmethod
    def available(cls) -> list[str]:
        _ensure_default_brokers()
        return sorted(cls._factories)

    @classmethod
    def unregister_all(cls) -> None:
        cls._factories.clear()


_DEFAULT_BROKERS_REGISTERED = False


def _ensure_default_brokers() -> None:
    global _DEFAULT_BROKERS_REGISTERED
    if _DEFAULT_BROKERS_REGISTERED:
        return
    _DEFAULT_BROKERS_REGISTERED = True
    try:
        from ntrade.brokers.dhan import DhanBroker
        BrokerRegistry.register("dhan", lambda **kw: DhanBroker(**kw))
    except ImportError:
        pass  # Dhan-Tradehull not installed — skip
    try:
        from ntrade.brokers.paper import PaperBroker
        BrokerRegistry.register("paper", lambda **kw: PaperBroker(**kw))
    except ImportError:
        pass


def register_default_brokers() -> None:
    """Eagerly register default brokers (backward-compatible public API)."""
    _ensure_default_brokers()
