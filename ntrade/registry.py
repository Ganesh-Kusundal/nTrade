"""Registry — Flyweight + Registry patterns.

SymbolMaster caches instrument instances so the same symbol always resolves to
the same object (shared metadata, subscriptions, cache state).
BrokerRegistry maps broker names to factory callables.

Indicator/strategy registries live here too: tiny write-once dict registries so
adding a new indicator or strategy needs ZERO call-site edits. Registration
happens at import time (side-effect of importing the module that defines the
indicator/strategy); after the first ``get()`` the registry is frozen so late
registration is a loud failure instead of a silent miss.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Type

from ntrade.domain.instruments.base import Instrument


class SymbolMaster:
    """Flyweight: shared instrument instances keyed by (kind, symbol, exchange).

    The cache is guarded by a reentrant lock so multi-threaded factories (feed
    thread vs. strategy thread) never race dict mutation (D-007 class).
    """

    def __init__(self):
        import threading
        self._cache: dict[tuple, Instrument] = {}
        self._lock = threading.RLock()

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
        with self._lock:
            if force_new or key not in self._cache:
                inst = cls(symbol, exchange=exchange, **specs)
                self._cache[key] = inst
            return self._cache[key]

    def invalidate(self, symbol: str, exchange: str | None = None) -> None:
        with self._lock:
            for key in list(self._cache.keys()):
                if key[1] == symbol and (exchange is None or key[2] == exchange):
                    del self._cache[key]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


class BrokerRegistry:
    """Registry of broker factories: BrokerRegistry.get("dhan") -> DhanBroker.

    The factory map and the default-broker flag are shared class state, so
    all mutation is guarded by a class-level RLock — concurrent factories
    (feed thread vs. strategy thread) cannot race dict writes (L4)."""

    _factories: dict[str, Callable] = {}
    _lock = threading.RLock()

    @classmethod
    def register(cls, name: str, factory: Callable) -> None:
        with cls._lock:
            cls._factories[name] = factory

    @classmethod
    def get(cls, name: str, **kwargs):
        with cls._lock:
            _ensure_default_brokers()
            if name not in cls._factories:
                raise KeyError(f"No broker registered under {name!r}; available: {sorted(cls._factories)}")
            factory = cls._factories[name]
        return factory(**kwargs)

    @classmethod
    def available(cls) -> list[str]:
        with cls._lock:
            _ensure_default_brokers()
            return sorted(cls._factories)

    @classmethod
    def unregister_all(cls) -> None:
        """Clear all registered brokers and reset the default-broker flag so a
        later ``get()`` re-registers the defaults (test isolation)."""
        global _DEFAULT_BROKERS_REGISTERED
        with cls._lock:
            cls._factories.clear()
            _DEFAULT_BROKERS_REGISTERED = False


_DEFAULT_BROKERS_REGISTERED = False


def _ensure_default_brokers() -> None:
    global _DEFAULT_BROKERS_REGISTERED
    with BrokerRegistry._lock:
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


# ---------------------------------------------------------------------------
# Indicator + strategy registries (Phase A: tiny write-once dict registries)
# ---------------------------------------------------------------------------


class Registry:
    """Tiny write-once registry: ``register(id, spec)`` then ``get(id)``.

    ``register`` after the first ``get`` raises ``RuntimeError`` — once the
    registry has been consumed it is frozen, so a late/stray registration can't
    silently shadow or miss an entry that a caller already resolved.
    """

    def __init__(self):
        self._items: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._frozen = False

    def register(self, id: str, spec: Any) -> Any:
        with self._lock:
            if self._frozen:
                raise RuntimeError(
                    f"registry {self!r} is frozen: cannot register {id!r} "
                    f"after first get()"
                )
            self._items[id] = spec
            return spec

    def get(self, id: str) -> Any:
        with self._lock:
            self._frozen = True
            if id not in self._items:
                raise KeyError(
                    f"no entry registered under {id!r}; "
                    f"available: {sorted(self._items)}"
                )
            return self._items[id]

    @property
    def registry(self) -> dict[str, Any]:
        """Read-only snapshot of the registered specs (by id)."""
        with self._lock:
            return dict(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __contains__(self, id: str) -> bool:
        with self._lock:
            return id in self._items

    def __repr__(self) -> str:
        return f"Registry({sorted(self._items)!r})"


@dataclass
class PlotSpec:
    """How the UI renders a scalar indicator series."""

    series_key: str            # column name produced by compute_bundle
    pane: str = "overlay"       # "overlay" | "separate"
    color: str | None = None


@dataclass
class IndicatorSpec:
    """Contract for one computable indicator.

    ``params`` are the default kwargs forwarded to ``compute_bundle`` (or the
    indicator fn directly). The calculator itself is *not* duplicated here —
    the registry points at the existing ``compute_bundle`` / indicator fn so the
    warm-up logic in ``IndicatorEngine`` is reused verbatim (single source of
    truth for the math).
    """

    id: str
    label: str
    params: dict[str, Any] = field(default_factory=dict)
    series: bool = True
    plot: PlotSpec | None = None


@dataclass
class StrategySpec:
    """Contract for one strategy.

    ``params`` mirror the strategy ``__init__`` defaults so a caller can
    instantiate from the spec alone: ``cls(**spec.params)``.
    ``indicators`` lists the indicator ids the strategy reads (for UI overlay
    discovery; backend compute is driven by ``compute_bundle`` params).
    """

    id: str
    label: str
    params: dict[str, Any] = field(default_factory=dict)
    indicators: list[str] = field(default_factory=list)


indicator = Registry()
strategy = Registry()
