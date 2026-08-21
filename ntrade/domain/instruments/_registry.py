"""_registry — lazy instrument registry to break chain -> derivatives eager edge.

chain.py previously did ``from .derivatives import Option, Future`` at import time,
creating an eager edge ``chain -> derivatives -> base``.  This module exists as
an explicit break point: it holds no eager imports itself and can be used for
lazy lookups if future cycles emerge.

Current fix keeps ``chain.py``'s derivative imports under ``TYPE_CHECKING`` only,
so this registry is intentionally minimal.  It is retained to satisfy the
refactoring plan's "create if needed" step and to provide a single place for
deferred resolution if needed later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ntrade.domain.instruments.derivatives import Future, Option

# Optional lazy lookup dict — populated on first access, never at import time.
_LAZY: dict[str, type] = {}


def get_option_cls():
    """Return ``Option`` without creating an eager import cycle."""
    if "Option" not in _LAZY:
        from ntrade.domain.instruments.derivatives import Option  # local import
        _LAZY["Option"] = Option
    return _LAZY["Option"]


def get_future_cls():
    """Return ``Future`` without creating an eager import cycle."""
    if "Future" not in _LAZY:
        from ntrade.domain.instruments.derivatives import Future  # local import
        _LAZY["Future"] = Future
    return _LAZY["Future"]
