"""Unit tests for dhan_auth helpers (no live network).

Tradehull is monkeypatched via dhan_auth.Tradehull (module-level import), so
these tests never touch the real Dhan API.
"""

import base64
import json
import time

import pytest

from ntrade.brokers import dhan_auth


def make_token(exp: int) -> str:
    """Build a JWT-shaped token whose payload is parseable by jwt_expiry."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


class FakeTradehull:
    """Mirrors the real library: instrument_df/Dhan only exist after a real login."""

    def __init__(self, client_code, token_id="", mode="access_token", **kwargs):
        self.client_code = client_code
        self.token_id = token_id
        self.mode = mode
        self.kwargs = kwargs

    def _mark_logged_in(self):
        self.instrument_df = "file"
        self.Dhan = "dhan"


def test_jwt_expiry_parsing():
    exp, when = dhan_auth.jwt_expiry(make_token(1_800_000_000))
    assert exp == 1_800_000_000
    assert "2027" in when


def test_jwt_expiry_unparseable():
    assert dhan_auth.jwt_expiry("not-a-jwt") == (None, None)


def test_shared_store_token(tmp_path):
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(9_999_999_999)}))
    token = dhan_auth._token_from_shared_store(str(store))
    assert token is not None
    assert token.count(".") == 2


def test_shared_store_expired_token(tmp_path):
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(1)}))  # long expired
    assert dhan_auth._token_from_shared_store(str(store)) is None


def test_cooldown_active(tmp_path):
    cooldown = tmp_path / "cooldown.json"
    cooldown.write_text(json.dumps({"last_attempt_at": time.time()}))
    assert dhan_auth._cooldown_active(str(cooldown))
    cooldown.write_text(json.dumps({"last_attempt_at": time.time() - 1000}))
    assert not dhan_auth._cooldown_active(str(cooldown))
    assert not dhan_auth._cooldown_active("/nonexistent/path.json")


def test_persist_shared(tmp_path):
    token_path = tmp_path / "token.json"
    cooldown_path = tmp_path / "cooldown.json"
    dhan_auth._persist_shared(str(token_path), str(cooldown_path), make_token(9_999_999_999))
    assert token_path.exists()
    assert cooldown_path.exists()
    saved = json.loads(token_path.read_text())
    assert saved["access_token"].count(".") == 2


def test_login_ok_detection():
    good = FakeTradehull("123")
    good._mark_logged_in()
    assert dhan_auth._login_ok(good)
    bad = FakeTradehull("123")
    assert not dhan_auth._login_ok(bad)


def test_get_tradehull_uses_shared_store(monkeypatch, tmp_path):
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(9_999_999_999)}))
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": make_token(9_999_999_999),
        "DHAN_TOKEN_PATH": str(store),
        "DHAN_COOLDOWN_PATH": str(tmp_path / "cooldown.json"),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    calls = []

    def fake_tradehull(client_code, token_id="", mode="access_token", **kwargs):
        calls.append((client_code, token_id, mode))
        t = FakeTradehull(client_code, token_id, mode)
        if token_id == dhan_auth._token_from_shared_store(str(store)):
            t._mark_logged_in()
        return t

    monkeypatch.setattr(dhan_auth, "Tradehull", fake_tradehull)
    tsl = dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    assert calls[0][1] == dhan_auth._token_from_shared_store(str(store))  # shared store preferred
    assert tsl.mode == "access_token"


def test_get_tradehull_falls_back_to_pin_totp(monkeypatch, tmp_path):
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": "expired.token.xxx",  # unparseable -> exp None -> attempted, fails
        "DHAN_TOKEN_PATH": str(tmp_path / "missing.json"),
        "DHAN_COOLDOWN_PATH": str(tmp_path / "cooldown.json"),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    calls = []

    def fake_tradehull(client_code, token_id="", mode="access_token", **kwargs):
        calls.append((client_code, token_id, mode))
        t = FakeTradehull(client_code, token_id, mode)
        if mode == "pin_totp":
            t.token_id = make_token(9_999_999_999)
            t._mark_logged_in()
        return t

    monkeypatch.setattr(dhan_auth, "Tradehull", fake_tradehull)
    tsl = dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    # Unparseable token (exp None) IS attempted; the shared store is empty.
    assert ("12345", "expired.token.xxx", "access_token") in calls
    assert any(mode == "pin_totp" for _, _, mode in calls)
    assert tsl.token_id.count(".") == 2  # fresh token returned


def test_persist_shared_sets_restrictive_permissions(tmp_path):
    import os, json
    from ntrade.brokers.dhan_auth import _persist_shared
    token_path = str(tmp_path / "token.json")
    _persist_shared(token_path, "", "fake.jwt.token")
    mode = os.stat(token_path).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
