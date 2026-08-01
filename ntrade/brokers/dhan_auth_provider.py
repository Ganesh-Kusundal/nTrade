"""DhanAuthProvider — class-based wrapper over the dhan_auth module.

Provides a clean authentication interface that the DhanBroker composes::

    auth = DhanAuthProvider(env_path=".env")
    tsl = auth.authenticate()   # returns a connected Tradehull instance
    tsl = auth.tsl              # cached property after authenticate()

The provider automatically refreshes expired tokens using PIN+TOTP when needed,
handling Dhan's 24-hour token lifetime transparently.  A background timer
proactively refreshes the token before expiry (mirroring testTrade's
TokenManager._schedule_proactive_refresh pattern) so no API call ever hits
an expired token.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ntrade.brokers.dhan_auth import get_tradehull, jwt_expiry, EXPIRY_BUFFER_S

logger = logging.getLogger(__name__)


class DhanAuthProvider:
    """Manages Dhan authentication lifecycle with automatic token refresh.

    Wraps the module-level ``get_tradehull`` function in a class so the broker
    can hold a reference, lazy-connect, and re-authenticate if needed.

    Dhan APP tokens expire after 24 hours and cannot be renewed. This provider
    detects expiry proactively and refreshes via PIN+TOTP automatically.
    A background timer fires before expiry to refresh silently, mirroring
    testTrade's ``TokenManager._schedule_proactive_refresh``.
    """

    def __init__(self, env_path: str = ".env", env: dict | None = None):
        self._env_path = env_path
        self._env = env
        self._tsl: Any = None
        self._refresh_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def authenticate(self) -> Any:
        """Run the authentication flow and return a connected Tradehull.

        Automatically handles token expiry by falling back to PIN+TOTP.
        Schedules a proactive background refresh after successful auth.
        """
        with self._lock:
            self._tsl = get_tradehull(env=self._env, env_path=self._env_path)
            self._schedule_proactive_refresh()
        return self._tsl

    def refresh_if_needed(self) -> Any:
        """Check if the current token is expired/near-expiry and refresh if needed.

        Returns the current or refreshed Tradehull instance.
        Call this before critical operations to ensure a valid token.
        Thread-safe: only one thread enters the refresh path.
        """
        if self._tsl is None:
            return self.authenticate()

        token = getattr(self._tsl, "token_id", None)
        if token:
            exp, _ = jwt_expiry(token)
            if exp is not None and int(time.time()) > (exp - EXPIRY_BUFFER_S):
                return self.authenticate()

        return self._tsl

    def stop(self) -> None:
        """Cancel the proactive refresh timer. Called during shutdown."""
        self._cancel_proactive_refresh()

    @property
    def tsl(self) -> Any:
        """The authenticated Tradehull instance (None until ``authenticate``)."""
        return self._tsl

    @property
    def is_authenticated(self) -> bool:
        return self._tsl is not None

    def time_until_expiry(self) -> float:
        """Seconds until the current token expires. Returns 0 if no token."""
        if self._tsl is None:
            return 0.0
        token = getattr(self._tsl, "token_id", None)
        if not token:
            return 0.0
        exp, _ = jwt_expiry(token)
        if exp is None:
            return 0.0
        remaining = exp - int(time.time())
        return max(0.0, float(remaining))

    # ── Proactive background refresh ────────────────────────────────────

    def _schedule_proactive_refresh(self) -> None:
        """Schedule a background timer to refresh the token before expiry.

        Mirrors testTrade's TokenManager._schedule_proactive_refresh: the
        timer fires EXPIRY_BUFFER_S seconds before the token expires,
        giving PIN+TOTP time to mint a fresh token silently.
        """
        self._cancel_proactive_refresh()
        remaining = self.time_until_expiry()
        if remaining <= 0:
            return
        delay = max(0.0, remaining - EXPIRY_BUFFER_S)
        if delay < 1.0:
            return
        self._refresh_timer = threading.Timer(delay, self._proactive_refresh)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()
        logger.debug("proactive_refresh_scheduled: delay=%.0fs expiry=%.0fs",
                     delay, remaining)

    def _proactive_refresh(self) -> None:
        """Timer callback: refresh, logging failures instead of raising."""
        try:
            self.authenticate()
            logger.info("proactive_token_refresh_ok: expiry=%.0fs",
                        self.time_until_expiry())
        except Exception as exc:
            logger.warning("proactive_token_refresh_failed: %s", exc)

    def _cancel_proactive_refresh(self) -> None:
        timer = self._refresh_timer
        if timer is not None:
            timer.cancel()
            self._refresh_timer = None

    def __repr__(self) -> str:
        status = "authenticated" if self._tsl else "not authenticated"
        return f"DhanAuthProvider({status})"
