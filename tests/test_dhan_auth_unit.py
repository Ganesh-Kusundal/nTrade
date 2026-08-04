"""Unit tests for dhan_auth SoT contract (no live network).

Tradehull is monkeypatched via dhan_auth.Tradehull (module-level import).
"""

from __future__ import annotations

import base64
import json
import os
import time

import pytest

from ntrade.brokers import dhan_auth


def make_token(exp: int) -> str:
    """Build a JWT-shaped token whose payload is parseable by jwt_expiry."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


FAR = 9_999_999_999


class FakeTradehull:
    """Controllable Tradehull stand-in for SoT / alive-check tests."""

    # Class knobs: which tokens are data-plane-alive
    alive_tokens: set[str] = set()
    # Tokens that print Invalid Token on LTP (instrument_df still set)
    invalid_tokens: set[str] = set()
    # Force historical-only success (LTP empty, history ok)
    history_only_tokens: set[str] = set()

    def __init__(self, client_code, token_id="", mode="access_token", **kwargs):
        self.client_code = client_code
        self.token_id = token_id
        self.mode = mode
        self.kwargs = kwargs
        if mode == "pin_totp":
            # Distinct from store/seed JWTs (make_token(exp) is deterministic).
            self.token_id = make_token(FAR - 7)
            FakeTradehull.alive_tokens.add(self.token_id)
            self._mark_logged_in()
        elif token_id:
            # Mirror library: attributes appear after "login"
            self._mark_logged_in()

    def _mark_logged_in(self):
        self.instrument_df = "file"
        self.Dhan = "dhan"

    def get_ltp_data(self, names=None):
        tok = self.token_id
        if tok in FakeTradehull.invalid_tokens:
            print({"status": "failure", "remarks": {
                "error_code": "DH-906", "error_message": "Invalid Token",
            }})
            return {}
        if tok in FakeTradehull.history_only_tokens:
            return {}
        if tok in FakeTradehull.alive_tokens or self.mode == "pin_totp":
            if self.mode == "pin_totp":
                FakeTradehull.alive_tokens.add(tok)
            return {"NIFTY": 24000.0}
        return {}

    def get_historical_data(self, tradingsymbol="", exchange="", timeframe=""):
        tok = self.token_id
        if tok in FakeTradehull.invalid_tokens:
            print({"status": "failure", "remarks": {
                "error_code": "DH-906", "error_message": "Invalid Token",
            }})
            return None
        if tok in FakeTradehull.alive_tokens or tok in FakeTradehull.history_only_tokens:
            return [{"close": 1}]  # len > 0
        if self.mode == "pin_totp":
            return [{"close": 1}]
        return None


@pytest.fixture(autouse=True)
def _reset_fake_knobs():
    FakeTradehull.alive_tokens = set()
    FakeTradehull.invalid_tokens = set()
    FakeTradehull.history_only_tokens = set()
    yield


def test_jwt_expiry_parsing():
    exp, when = dhan_auth.jwt_expiry(make_token(1_800_000_000))
    assert exp == 1_800_000_000
    assert "2027" in when


def test_jwt_expiry_unparseable():
    assert dhan_auth.jwt_expiry("not-a-jwt") == (None, None)


def test_shared_store_token(tmp_path):
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(FAR)}))
    token = dhan_auth._token_from_shared_store(str(store))
    assert token is not None
    assert token.count(".") == 2


def test_shared_store_expired_token(tmp_path):
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(1)}))
    assert dhan_auth._token_from_shared_store(str(store)) is None


def test_shared_store_near_expiry_skipped(tmp_path):
    # Within EXPIRY_BUFFER_S of now → treated as unusable
    near = int(time.time()) + 60
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(near)}))
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
    dhan_auth._persist_shared(str(token_path), str(cooldown_path), make_token(FAR))
    assert token_path.exists()
    assert cooldown_path.exists()
    saved = json.loads(token_path.read_text())
    assert saved["access_token"].count(".") == 2


def test_clear_shared(tmp_path):
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": make_token(FAR), "expires_at": FAR}))
    dhan_auth._clear_shared(str(store))
    assert dhan_auth._token_from_shared_store(str(store)) is None
    assert json.loads(store.read_text())["access_token"] == ""


def test_login_ok_requires_data_plane():
    dead = FakeTradehull("123", token_id=make_token(FAR))
    assert not dhan_auth._login_ok(dead)  # attributes set, but no LTP/history

    bad = FakeTradehull("123")
    assert not dhan_auth._login_ok(bad)  # no attributes

    tok = make_token(FAR)
    FakeTradehull.alive_tokens.add(tok)
    good = FakeTradehull("123", token_id=tok)
    assert dhan_auth._login_ok(good)


def test_login_ok_rejects_invalid_token_print():
    tok = make_token(FAR)
    FakeTradehull.invalid_tokens.add(tok)
    t = FakeTradehull("123", token_id=tok)
    assert not dhan_auth._login_ok(t)


def test_login_ok_history_fallback():
    tok = make_token(FAR)
    FakeTradehull.history_only_tokens.add(tok)
    t = FakeTradehull("123", token_id=tok)
    assert dhan_auth._login_ok(t)


def test_store_token_alive_no_totp(monkeypatch, tmp_path):
    store_tok = make_token(FAR)
    FakeTradehull.alive_tokens.add(store_tok)
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": store_tok}))
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": make_token(FAR),
        "DHAN_TOKEN_PATH": str(store),
        "DHAN_COOLDOWN_PATH": str(tmp_path / "cooldown.json"),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    calls = []

    def fake_tradehull(client_code, token_id="", mode="access_token", **kwargs):
        calls.append((client_code, token_id, mode))
        return FakeTradehull(client_code, token_id, mode, **kwargs)

    monkeypatch.setattr(dhan_auth, "Tradehull", fake_tradehull)
    tsl = dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    assert calls[0][1] == store_tok
    assert all(mode != "pin_totp" for _, _, mode in calls)
    assert tsl.token_id == store_tok
    # SoT rewritten on success
    assert json.loads(store.read_text())["access_token"] == store_tok


def test_dead_store_invalid_token_clears_and_falls_to_seed(monkeypatch, tmp_path):
    dead = make_token(FAR)
    seed = make_token(FAR - 1000)
    FakeTradehull.invalid_tokens.add(dead)
    FakeTradehull.alive_tokens.add(seed)
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": dead, "expires_at": FAR}))
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": seed,
        "DHAN_TOKEN_PATH": str(store),
        "DHAN_COOLDOWN_PATH": str(tmp_path / "cooldown.json"),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    calls = []

    def fake_tradehull(client_code, token_id="", mode="access_token", **kwargs):
        calls.append((client_code, token_id, mode))
        return FakeTradehull(client_code, token_id, mode, **kwargs)

    monkeypatch.setattr(dhan_auth, "Tradehull", fake_tradehull)
    tsl = dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    assert ("12345", dead, "access_token") in calls
    assert ("12345", seed, "access_token") in calls
    assert all(mode != "pin_totp" for _, _, mode in calls)
    assert tsl.token_id == seed
    # Seed promoted into SoT
    assert json.loads(store.read_text())["access_token"] == seed


def test_empty_store_alive_seed_persists(monkeypatch, tmp_path):
    seed = make_token(FAR)
    FakeTradehull.alive_tokens.add(seed)
    store = tmp_path / "token.json"
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": seed,
        "DHAN_TOKEN_PATH": str(store),
        "DHAN_COOLDOWN_PATH": str(tmp_path / "cooldown.json"),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }

    def fake_tradehull(client_code, token_id="", mode="access_token", **kwargs):
        return FakeTradehull(client_code, token_id, mode, **kwargs)

    monkeypatch.setattr(dhan_auth, "Tradehull", fake_tradehull)
    tsl = dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    assert tsl.token_id == seed
    assert store.exists()
    assert json.loads(store.read_text())["access_token"] == seed
    assert json.loads(store.read_text())["source"] == "ENV"


def test_dead_store_dead_seed_totp_persists(monkeypatch, tmp_path):
    dead = make_token(FAR - 1)
    FakeTradehull.invalid_tokens.add(dead)
    store = tmp_path / "token.json"
    store.write_text(json.dumps({"access_token": dead}))
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": "expired.token.xxx",  # unparseable → attempted, fails alive
        "DHAN_TOKEN_PATH": str(store),
        "DHAN_COOLDOWN_PATH": str(tmp_path / "cooldown.json"),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    calls = []

    def fake_tradehull(client_code, token_id="", mode="access_token", **kwargs):
        calls.append((client_code, token_id, mode))
        return FakeTradehull(client_code, token_id, mode, **kwargs)

    monkeypatch.setattr(dhan_auth, "Tradehull", fake_tradehull)
    tsl = dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    assert any(mode == "pin_totp" for _, _, mode in calls)
    assert tsl.token_id.count(".") == 2
    saved = json.loads(store.read_text())
    assert saved["access_token"] == tsl.token_id
    assert saved["source"] == "TOTP"


def test_cooldown_blocks_totp(monkeypatch, tmp_path):
    cooldown = tmp_path / "cooldown.json"
    cooldown.write_text(json.dumps({"last_attempt_at": time.time()}))
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": "",
        "DHAN_TOKEN_PATH": str(tmp_path / "missing.json"),
        "DHAN_COOLDOWN_PATH": str(cooldown),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    monkeypatch.setattr(dhan_auth, "Tradehull", FakeTradehull)
    with pytest.raises(ConnectionError, match="cooldown"):
        dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))


def test_failed_totp_mint_arms_cooldown(monkeypatch, tmp_path):
    """M-5: a mint that raises must arm the cooldown — otherwise a wrong
    PIN/TOTP can be hammered in a tight retry loop and lock the account."""
    cooldown = tmp_path / "cooldown.json"
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": "",
        "DHAN_TOKEN_PATH": str(tmp_path / "missing.json"),
        "DHAN_COOLDOWN_PATH": str(cooldown),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }

    def raising_tradehull(client_code, token_id="", mode="access_token", **kwargs):
        if mode == "pin_totp":
            raise RuntimeError("wrong TOTP")
        return FakeTradehull(client_code, token_id, mode, **kwargs)

    monkeypatch.setattr(dhan_auth, "Tradehull", raising_tradehull)
    with pytest.raises(ConnectionError, match="mint raised"):
        dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    assert cooldown.exists()
    saved = json.loads(cooldown.read_text())
    assert time.time() - float(saved["last_attempt_at"]) < 5
    # the armed cooldown now blocks an immediate retry
    monkeypatch.setattr(dhan_auth, "Tradehull", FakeTradehull)
    with pytest.raises(ConnectionError, match="cooldown"):
        dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))


def test_dead_totp_mint_arms_cooldown(monkeypatch, tmp_path):
    """M-5: a mint whose data-plane alive check fails also arms the cooldown."""

    class DeadPinTotp(FakeTradehull):
        def __init__(self, client_code, token_id="", mode="access_token", **kwargs):
            super().__init__(client_code, token_id, mode, **kwargs)
            if mode == "pin_totp":
                FakeTradehull.alive_tokens.discard(self.token_id)

        def get_ltp_data(self, names=None):
            return {} if self.mode == "pin_totp" else super().get_ltp_data(names)

        def get_historical_data(self, tradingsymbol="", exchange="", timeframe=""):
            return None if self.mode == "pin_totp" else super().get_historical_data(
                tradingsymbol, exchange, timeframe)

    cooldown = tmp_path / "cooldown.json"
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": "",
        "DHAN_TOKEN_PATH": str(tmp_path / "missing.json"),
        "DHAN_COOLDOWN_PATH": str(cooldown),
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    monkeypatch.setattr(dhan_auth, "Tradehull", DeadPinTotp)
    with pytest.raises(ConnectionError, match="PIN\+TOTP login failed"):
        dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))
    assert cooldown.exists()
    assert dhan_auth._cooldown_active(str(cooldown))


def test_no_pin_totp_raises(monkeypatch, tmp_path):
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": "",
        "DHAN_TOKEN_PATH": str(tmp_path / "missing.json"),
        "DHAN_COOLDOWN_PATH": str(tmp_path / "cooldown.json"),
    }
    monkeypatch.setattr(dhan_auth, "Tradehull", FakeTradehull)
    with pytest.raises(ConnectionError, match="DHAN_PIN"):
        dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"))


def test_persist_shared_sets_restrictive_permissions(tmp_path):
    token_path = str(tmp_path / "token.json")
    dhan_auth._persist_shared(token_path, "", "fake.jwt.token")
    mode = os.stat(token_path).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ============================================================ B-012: gated probes
class FakeGate:
    """Records which quota classes were acquired (no real timing)."""

    def __init__(self):
        self.acquired: list = []

    def acquire(self, quota):
        self.acquired.append(quota)


def test_login_ok_consumes_quote_quota():
    """With a gate injected, _login_ok's LTP probe pays the QUOTE class."""
    from ntrade.execution.rate_limit import Quota
    gate = FakeGate()
    tok = make_token(FAR)
    FakeTradehull.alive_tokens.add(tok)
    t = FakeTradehull("123", token_id=tok)
    assert dhan_auth._login_ok(t, gate=gate)
    assert gate.acquired == [Quota.QUOTE]


def test_login_ok_consumes_quote_then_data_on_fallback():
    """History-fallback path acquires QUOTE first, then DATA."""
    from ntrade.execution.rate_limit import Quota
    gate = FakeGate()
    tok = make_token(FAR)
    FakeTradehull.history_only_tokens.add(tok)
    t = FakeTradehull("123", token_id=tok)
    assert dhan_auth._login_ok(t, gate=gate)
    assert gate.acquired == [Quota.QUOTE, Quota.DATA]


def test_login_ok_gate_none_is_noop():
    """gate=None (default) changes nothing — probes run unthrottled."""
    tok = make_token(FAR)
    FakeTradehull.alive_tokens.add(tok)
    t = FakeTradehull("123", token_id=tok)
    assert dhan_auth._login_ok(t)  # no gate, no crash


def test_get_tradehull_forwards_gate_to_login_ok(monkeypatch, tmp_path):
    """The broker's gate flows connect -> authenticate -> get_tradehull -> _login_ok."""
    from ntrade.execution.rate_limit import Quota
    gate = FakeGate()
    env = {
        "DHAN_CLIENT_ID": "12345",
        "DHAN_ACCESS_TOKEN": make_token(FAR),
        "DHAN_TOKEN_PATH": "",
        "DHAN_COOLDOWN_PATH": "",
        "DHAN_PIN": "960000",
        "DHAN_TOTP_SECRET": "AAAA",
    }
    monkeypatch.setattr(dhan_auth, "Tradehull", FakeTradehull)
    tsl = dhan_auth.get_tradehull(env=env, env_path=str(tmp_path / "nope.env"), gate=gate)
    assert tsl is not None
    assert Quota.QUOTE in gate.acquired
