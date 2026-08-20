"""Shared Dhan authentication helper — single source of truth.

Reads the same vars as check_connection.py / Trade_XV2:
    DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN (bootstrap seed only), DHAN_PIN,
    DHAN_TOTP_SECRET, DHAN_TOKEN_PATH (canonical cache), DHAN_COOLDOWN_PATH.

Strategy (one cache, not dual SoT):
  1. Try token from DHAN_TOKEN_PATH if present and not near expiry.
  2. Else try DHAN_ACCESS_TOKEN as bootstrap seed only.
  3. Else mint via PIN+TOTP (respecting cooldown).
  Every successful path persists the working token into DHAN_TOKEN_PATH.
  A store token that fails the data-plane alive check is cleared before fallthrough.

Dhan APP tokens have a hard 24-hour lifetime and CANNOT be renewed (DH-905 error).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_quiet_lock = threading.Lock()

from ntrade.execution.rate_limit import Quota
from ntrade.execution._guard import TotpCooldownGuard, TotpRateLimitError

TOTP_ATTEMPT_COOLDOWN_S = 90
# Proactive expiry buffer: refresh token if it expires within this window
# Default: 15 minutes (900 seconds) to avoid mid-session expiry
EXPIRY_BUFFER_S = int(os.environ.get("DHAN_EXPIRY_BUFFER_S", "900"))

# Imported at module level (guarded) so tests can monkeypatch dhan_auth.Tradehull
# while importing ntrade never hard-depends on the Dhan library being installed.
try:
    from Dhan_Tradehull import Tradehull  # noqa: F401
except ImportError:  # pragma: no cover - import guard for non-Dhan installs
    Tradehull = None  # type: ignore[assignment]


# auth lifetime is independent of observability — stopping auth never emits
# heartbeat/feed/order events (see tests/test_contract_auth_observability.py)
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


def _token_usable(token: str) -> bool:
    """True when JWT is parseable and not within EXPIRY_BUFFER_S of expiry."""
    exp, _ = jwt_expiry(token)
    if exp is None:
        return True  # unparseable — let the data plane decide
    return int(time.time()) <= (exp - EXPIRY_BUFFER_S)


def _token_from_shared_store(token_path: str) -> str | None:
    """Return token from SoT store if it exists and is not near expiry."""
    try:
        data = json.loads(Path(token_path).read_text())
        token = (data.get("access_token") or "").strip()
        if not token:
            return None
        exp, _ = jwt_expiry(token)
        if exp is None:
            return None
        if int(time.time()) > (exp - EXPIRY_BUFFER_S):
            return None
        return token
    except Exception:
        return None


def _totp_guard(cooldown_path: str) -> TotpCooldownGuard | None:
    """Build a v3 TotpCooldownGuard bound to the same state file nTrade uses.

    Falls back to None when no cooldown_path is configured so PaperBroker /
    test callers keep working without a state file on disk.
    """
    if not cooldown_path:
        return None
    try:
        return TotpCooldownGuard(broker="dhan", cooldown_seconds=TOTP_ATTEMPT_COOLDOWN_S,
                                 state_path=Path(cooldown_path))
    except Exception:
        return None


def _cooldown_active(cooldown_path: str) -> bool:
    """Check if TOTP mint is rate-limited by a prior attempt.

    Delegates to v3's cross-process TotpCooldownGuard (file-locked state)
    instead of an in-process-only timestamp read. Returns True when the
    cooldown is active (no mint attempt allowed).
    """
    guard = _totp_guard(cooldown_path)
    if guard is None:
        return False
    try:
        guard.check_allowed()
        return False
    except TotpRateLimitError:
        return True
    except OSError:
        # Path doesn't exist / can't create lock file — no cooldown active.
        return False
    except Exception:
        return False


def _arm_cooldown(cooldown_path: str) -> None:
    """Record a TOTP attempt timestamp without touching the token store.

    M-5: failed mint attempts must arm the cooldown too — previously only a
    successful mint persisted it, so a wrong PIN/TOTP could be hammered in a
    tight retry loop and trip Dhan's anti-brute-force lockout.

    Delegates to v3's TotpCooldownGuard for cross-process atomicity. When the
    guard is unavailable (no state path), degrades to the legacy file write.
    """
    if not cooldown_path:
        return
    guard = _totp_guard(cooldown_path)
    if guard is not None:
        try:
            guard.acquire_attempt()
            return
        except TotpRateLimitError:
            # Already in cooldown from another process — that's fine; the
            # lock is held by the other attempt. Return without arming.
            return
    # Fallback: legacy plain-file write (no cross-process locking)
    try:
        last_success = 0.0
        try:
            last_success = float(
                json.loads(Path(cooldown_path).read_text()).get("last_success_at", 0))
        except Exception:
            pass
        Path(cooldown_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cooldown_path).write_text(json.dumps(
            {"broker": "dhan", "last_attempt_at": time.time(),
             "last_success_at": last_success}))
        os.chmod(cooldown_path, 0o600)
    except Exception:
        pass


def _persist_shared(token_path: str, cooldown_path: str, token: str, *, source: str = "TOTP") -> None:
    try:
        exp, _ = jwt_expiry(token)
        if token_path:
            state = {
                "access_token": token,
                "expires_at": float(exp or 0),
                "expires_at_ms": float(exp or 0) * 1000,
                "source": source,
            }
            Path(token_path).parent.mkdir(parents=True, exist_ok=True)
            Path(token_path).write_text(json.dumps(state))
            os.chmod(token_path, 0o600)
        # Record the successful login on the v3 TOTP cooldown guard so the
        # cooldown reflects a valid mint (not a failed attempt). This prevents
        # a wrong-PIN retry loop from being gated by its own failure.
        guard = _totp_guard(cooldown_path)
        if guard is not None:
            try:
                guard.record_success()
            except Exception:
                pass
        elif cooldown_path:
            # Fallback: legacy plain-file write (no cross-process locking)
            now = time.time()
            Path(cooldown_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cooldown_path).write_text(
                json.dumps({"broker": "dhan", "last_attempt_at": now, "last_success_at": now})
            )
            os.chmod(cooldown_path, 0o600)
    except Exception:
        pass


def _clear_shared(token_path: str) -> None:
    """Invalidate a dead SoT cache entry so the next call cannot reuse it."""
    if not token_path:
        return
    try:
        Path(token_path).parent.mkdir(parents=True, exist_ok=True)
        Path(token_path).write_text(json.dumps({"access_token": "", "expires_at": 0}))
        os.chmod(token_path, 0o600)
    except Exception:
        pass


def _invalid_token_signal(exc: BaseException | None = None, payload: object = None) -> bool:
    text = f"{exc!s} {payload!s}".lower()
    return "invalid token" in text or "dh-906" in text


def _login_ok_status(tsl, gate=None) -> tuple[bool, bool]:
    """Return (ok, is_invalid_token).

    Distinguishes a genuine invalid token (DH-906) from a transient data-plane
    hiccup (None/empty LTP/bars during weekend/maintenance).
    """
    if not (hasattr(tsl, "instrument_df") and hasattr(tsl, "Dhan")):
        return False, True

    out = io.StringIO()
    err = io.StringIO()
    try:
        if gate is not None:
            gate.acquire(Quota.QUOTE)
        with _quiet_lock:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                data = tsl.get_ltp_data(names=["NIFTY"])
        blob = out.getvalue() + err.getvalue()
        if _invalid_token_signal(payload=blob):
            return False, True
        if isinstance(data, dict) and data.get("NIFTY"):
            return True, False
    except Exception as exc:
        if _invalid_token_signal(exc):
            return False, True
        blob = out.getvalue() + err.getvalue()
        if _invalid_token_signal(payload=blob):
            return False, True

    # Weekend / empty LTP: fall back to one historical bar
    out2 = io.StringIO()
    err2 = io.StringIO()
    try:
        if gate is not None:
            gate.acquire(Quota.DATA)
        with _quiet_lock:
            with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
                df = tsl.get_historical_data(
                    tradingsymbol="RELIANCE", exchange="NSE", timeframe="5",
                )
        blob = out2.getvalue() + err2.getvalue()
        if _invalid_token_signal(payload=blob):
            return False, True
        if df is not None and len(df) > 0:
            return True, False
    except Exception as exc:
        if _invalid_token_signal(exc):
            return False, True
        blob = out2.getvalue() + err2.getvalue()
        if _invalid_token_signal(payload=blob):
            return False, True
    return False, False


def _login_ok(tsl, gate=None) -> bool:
    ok, _ = _login_ok_status(tsl, gate=gate)
    return ok


def _try_access_token(client_code: str, token: str):
    """Build Tradehull with an access token; suppress renew-noise on stderr."""
    with _quiet_lock:
        with contextlib.redirect_stderr(io.StringIO()):
            return Tradehull(client_code, token, mode="access_token")


def get_tradehull(env: dict | None = None, env_path: str = ".env", gate=None):
    """Create a connected Tradehull; SoT cache is DHAN_TOKEN_PATH.

    ``gate`` (optional :class:`BrokerRateGate`, B-012) is forwarded to
    ``_login_ok`` so login-time data-plane probes respect Quote/Data quotas.
    ``gate=None`` keeps standalone callers unchanged.
    """
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

    # 1) SoT store
    shared = _token_from_shared_store(token_path) if token_path else None
    if shared:
        tsl = _try_access_token(client_code, shared)
        ok, is_invalid = _login_ok_status(tsl, gate=gate)
        if ok:
            token = getattr(tsl, "token_id", None) or shared
            _persist_shared(token_path, cooldown_path, token, source="STORE")
            return tsl
        if is_invalid:
            _clear_shared(token_path)
        elif not (pin and totp_secret):
            # Transient data-plane failure with no TOTP credentials: keep store token
            return tsl

    # 2) Env seed (bootstrap only) — promote into store on success
    if access_token and _token_usable(access_token):
        tsl = _try_access_token(client_code, access_token)
        ok, is_invalid = _login_ok_status(tsl, gate=gate)
        if ok:
            token = getattr(tsl, "token_id", None) or access_token
            _persist_shared(token_path, cooldown_path, token, source="ENV")
            return tsl
        if not is_invalid and not (pin and totp_secret):
            return tsl

    # 3) PIN+TOTP mint
    if not (pin and totp_secret):
        raise ConnectionError(
            "Dhan login failed and DHAN_PIN / DHAN_TOTP_SECRET are missing — "
            "regenerate your access token from Dhan web → My Profile → API Access."
        )
    if _cooldown_active(cooldown_path):
        raise ConnectionError(f"Dhan TOTP login is on cooldown (see {cooldown_path}). Wait ~90s.")
    try:
        with _quiet_lock:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                tsl = Tradehull(client_code, mode="pin_totp", pin=pin, totp_secret=totp_secret)
    except Exception as exc:
        _arm_cooldown(cooldown_path)  # M-5: a failed mint arms the cooldown too
        raise ConnectionError(f"Dhan PIN+TOTP mint raised: {exc}") from exc
    ok, _ = _login_ok_status(tsl, gate=gate)
    if not ok:
        _arm_cooldown(cooldown_path)  # M-5: a dead mint arms the cooldown too
        raise ConnectionError("Dhan PIN+TOTP login failed. Check DHAN_PIN / DHAN_TOTP_SECRET.")
    _persist_shared(token_path, cooldown_path, tsl.token_id, source="TOTP")
    return tsl
