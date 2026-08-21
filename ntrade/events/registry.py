"""Event registry — auto-registration via __init_subclass__.

Provides a minimal manual registry for all frozen dataclass events.
Future path: hook into ``Event.__init_subclass__`` to auto-register
subclasses (``REGISTRY[cls.__name__] = cls`` on class creation).
Currently manual — call ``register(cls)`` or use as ``@register`` decorator.

Usage:
    from ntrade.events.registry import register, REGISTRY, all_events
    @register
    class MyEvent(Event): ...

Not imported anywhere yet — intentional to avoid risky base-class side effects.
"""
from __future__ import annotations

REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Register an event class by name and return it (decorator-friendly)."""
    REGISTRY[cls.__name__] = cls
    return cls


def all_events() -> dict[str, type]:
    """Return a shallow copy of the registry."""
    return dict(REGISTRY)
