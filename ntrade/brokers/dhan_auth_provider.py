"""DhanAuthProvider — class-based wrapper over the dhan_auth module.

Provides a clean authentication interface that the DhanBroker composes::

    auth = DhanAuthProvider(env_path=".env")
    tsl = auth.authenticate()   # returns a connected Tradehull instance
    tsl = auth.tsl              # cached property after authenticate()
"""

from __future__ import annotations

from typing import Any

from ntrade.brokers.dhan_auth import get_tradehull


class DhanAuthProvider:
    """Manages Dhan authentication lifecycle.

    Wraps the module-level ``get_tradehull`` function in a class so the broker
    can hold a reference, lazy-connect, and re-authenticate if needed.
    """

    def __init__(self, env_path: str = ".env", env: dict | None = None):
        self._env_path = env_path
        self._env = env
        self._tsl: Any = None

    def authenticate(self) -> Any:
        """Run the authentication flow and return a connected Tradehull."""
        self._tsl = get_tradehull(env=self._env, env_path=self._env_path)
        return self._tsl

    @property
    def tsl(self) -> Any:
        """The authenticated Tradehull instance (None until ``authenticate``)."""
        return self._tsl

    @property
    def is_authenticated(self) -> bool:
        return self._tsl is not None

    def __repr__(self) -> str:
        status = "authenticated" if self._tsl else "not authenticated"
        return f"DhanAuthProvider({status})"
