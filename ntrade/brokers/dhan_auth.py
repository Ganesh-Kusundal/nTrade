"""Shared Dhan authentication helper.

Reads the same vars as check_connection.py / Trade_XV2:
    DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, DHAN_AUTH_MODE, DHAN_PIN, DHAN_TOTP_SECRET,
    DHAN_TOKEN_PATH (shared store), DHAN_COOLDOWN_PATH.

Strategy:
  1. Prefer a valid token from the shared store (DHAN_TOKEN_PATH).
  2. Else use DHAN_ACCESS_TOKEN if not provably expired.
  3. Else fall back to PIN+TOTP (respecting the shared cooldown file) and persist
     the fresh token back to the shared store.
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

TOTP_ATTEMPT_COOLDOWN_S = 90

# Imported at module level (guarded) so tests can monkeypatch dhan_auth.Tradehull
# while importing ntrade never hard-depends on the Dhan library being installed.
try:
    from Dhan_Tradehull import Tradehull  # noqa: F401
except ImportError:  # pragma: no cover - import guard for non-Dhan installs
    Tradehull = None  # type: ignore[assignment]


def load_env(env_path: str = ".env") -> None:
    from dotenv import load_dotenv
    load_dotenv(Path(env_path).absolute())


def jwt_expiry(token: str):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = int(data.get("exp", 0))
        when = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()
        return exp, when.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return None, None


def _token_from_shared_store(token_path: str) -> str | None:
    try:
        data = json.loads(Path(token_path).read_text())
        token = (data.get("access_token") or "").strip()
        if not token:
            return None
        exp, _ = jwt_expiry(token)
        if exp is None or int(time.time()) > exp:
            return None
        return token
    except Exception:
        return None


def _cooldown_active(cooldown_path: str) -> bool:
    try:
        data = json.loads(Path(cooldown_path).read_text())
        last_attempt = float(data.get("last_attempt_at", 0))
        return int(time.time()) - last_attempt < TOTP_ATTEMPT_COOLDOWN_S
    except Exception:
        return False


def _persist_shared(token_path: str, cooldown_path: str, token: str) -> None:
    try:
        exp, _ = jwt_expiry(token)
        if token_path:
            state = {
                "access_token": token,
                "expires_at": float(exp or 0),
                "expires_at_ms": float(exp or 0) * 1000,
                "source": "TOTP",
            }
            Path(token_path).parent.mkdir(parents=True, exist_ok=True)
            Path(token_path).write_text(json.dumps(state))
            os.chmod(token_path, 0o600)
        if cooldown_path:
            now = time.time()
            Path(cooldown_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cooldown_path).write_text(
                json.dumps({"broker": "dhan", "last_attempt_at": now, "last_success_at": now})
            )
            os.chmod(cooldown_path, 0o600)
    except Exception:
        pass


def get_tradehull(env: dict | None = None, env_path: str = ".env"):
    """Create a connected Dhan_Tradehull.Tradehull instance using env credentials."""
    load_env(env_path)
    if env is None:
        env = os.environ

    client_code = (env.get("DHAN_CLIENT_ID") or "").strip()
    access_token = (env.get("DHAN_ACCESS_TOKEN") or "").strip()
    pin = (env.get("DHAN_PIN") or "").strip()
    totp_secret = (env.get("DHAN_TOTP_SECRET") or "").strip()
    token_path = (env.get("DHAN_TOKEN_PATH") or "").strip()
    cooldown_path = (env.get("DHAN_COOLDOWN_PATH") or "").strip()

    if not client_code:
        raise ValueError("DHAN_CLIENT_ID is not set")

    if Tradehull is None:
        raise ImportError("Dhan_Tradehull is not installed — run pip install Dhan-Tradehull")

    # 1) shared store first
    shared = _token_from_shared_store(token_path) if token_path else None
    if shared:
        tsl = Tradehull(client_code, shared, mode="access_token")
        if _login_ok(tsl):
            return tsl

    # 2) .env access token if not expired
    if access_token:
        exp, _ = jwt_expiry(access_token)
        if exp is None or int(time.time()) <= exp:
            tsl = Tradehull(client_code, access_token, mode="access_token")
            if _login_ok(tsl):
                return tsl

    # 3) PIN+TOTP fallback
    if not (pin and totp_secret):
        raise ConnectionError(
            "Dhan login failed and DHAN_PIN / DHAN_TOTP_SECRET are missing — "
            "regenerate your access token from Dhan web → My Profile → API Access."
        )
    if _cooldown_active(cooldown_path):
        raise ConnectionError(f"Dhan TOTP login is on cooldown (see {cooldown_path}). Wait ~90s.")
    tsl = Tradehull(client_code, mode="pin_totp", pin=pin, totp_secret=totp_secret)
    if not _login_ok(tsl):
        raise ConnectionError("Dhan PIN+TOTP login failed. Check DHAN_PIN / DHAN_TOTP_SECRET.")
    _persist_shared(token_path, cooldown_path, tsl.token_id)
    return tsl


def _login_ok(tsl) -> bool:
    # The library swallows login errors; success is marked by these attributes.
    return hasattr(tsl, "instrument_df") and hasattr(tsl, "Dhan")
