# Dhan Auto-Refresh Token Management — Implementation Summary

**Date:** 2026-08-01  
**Status:** ✅ Complete — All 616 tests passing  
**Task ID:** T-011 (kanban)

---

## Problem Statement

The Dhan broker connection was failing with a misleading error when the access token expired:

```
Token renew failed: Failed to renew token: {'errorType': 'Input_Exception', 
'errorCode': 'DH-905', 'errorMessage': 'Renewal of token not allowed for this token type'}
```

**Root cause:** Dhan APP tokens have a hard 24-hour lifetime and **cannot be renewed** via the API. The system had no proactive token management — it relied on one-shot authentication at startup with no mid-session refresh mechanism.

---

## Analysis: How testTrade (Reference Project) Manages Tokens

The reference project (`/Users/apple/Downloads/testTrade`) implements a sophisticated 3-layer token management system:

### 1. TokenManager (`scalpr/adapters/dhan/_auth.py`)
- **Thread-safe** token cache with `threading.Lock`
- **Proactive refresh:** Background `threading.Timer` fires 15 minutes before expiry
- **Daily file cache:** Tokens persisted to `runtime-dev/tokens/token_{client_id}_{date}.txt`
- **Cross-process adoption:** Re-reads cache before minting (another process may have minted)
- **TOTP rate-limit:** 125-second cooldown between PIN+TOTP attempts
- **Direct REST mint:** Calls `https://auth.dhan.co/app/generateAccessToken` directly (not via SDK)

### 2. DhanHttpClient (`scalpr/adapters/dhan/_http.py`)
- Calls `token_manager.get_token()` on **every HTTP request** (line 159)
- Token is always fresh when sent to the API
- Handles 401 by raising `DhanAuthError`

### 3. DhanClient (`scalpr/adapters/dhan/client.py`)
- Composes `TokenManager` at construction
- `_ws_token()` provides live token for WebSocket reconnects
- `start()` calls `token_manager.get_token()` at startup
- `_on_submit()` calls `token_manager.get_token()` before every order

**Key insight:** The token is **never stale** because every API call fetches a fresh token via `get_token()`, and a background timer refreshes 15 minutes before expiry.

---

## nTrade's Previous Architecture (The Problem)

nTrade wraps **Dhan-Tradehull** (third-party SDK) instead of calling the REST API directly:

```
DhanBroker
  ├── DhanAuthProvider (one-shot auth at startup)
  │     └── get_tradehull() → Tradehull instance
  ├── DhanTransport (wraps Tradehull API calls)
  └── self.tsl (direct reference, never refreshed)
```

**Issues:**
1. `DhanAuthProvider.authenticate()` runs **once** at startup
2. No mid-session refresh — if token dies mid-session, every API call fails
3. Tradehull library tries to renew internally and screams `DH-905` because APP tokens can't be renewed
4. No proactive background timer
5. No token freshness check before critical operations

---

## Solution: Auto-Refresh Token Management

Implemented a 3-layer solution mirroring testTrade's pattern, adapted for Tradehull:

### Layer 1: Proactive Expiry Buffer in `dhan_auth.py`

**File:** `ntrade/brokers/dhan_auth.py`

**Changes:**
- Added `EXPIRY_BUFFER_S = 900` (15 minutes) — configurable via `DHAN_EXPIRY_BUFFER_S` env var
- `_token_from_shared_store()` now rejects tokens within buffer of expiry
- `.env` access token check uses proactive buffer: `if exp is None or int(time.time()) <= (exp - EXPIRY_BUFFER_S)`
- Suppress Tradehull's misleading error output with `contextlib.redirect_stderr/stdout`
- Unparseable tokens (exp=None) are still attempted — let Dhan's 401 be the arbiter

**Code:**
```python
EXPIRY_BUFFER_S = int(os.environ.get("DHAN_EXPIRY_BUFFER_S", "900"))

def _token_from_shared_store(token_path: str) -> str | None:
    """Return token from shared store if it exists and is not near expiry."""
    # ... parse token ...
    exp, _ = jwt_expiry(token)
    if exp is None:
        return None
    # Proactive expiry check: reject token if it expires within buffer
    if int(time.time()) > (exp - EXPIRY_BUFFER_S):
        return None
    return token
```

### Layer 2: DhanAuthProvider with Background Timer

**File:** `ntrade/brokers/dhan_auth_provider.py`

**Changes:**
- Added `threading.Timer` for proactive background refresh
- `refresh_if_needed()` — thread-safe token freshness check
- `time_until_expiry()` — seconds until current token expires
- `_schedule_proactive_refresh()` — fires `EXPIRY_BUFFER_S` before expiry
- `_proactive_refresh()` — timer callback, logs failures instead of raising
- `stop()` — cancel timer during shutdown
- Thread-safe `authenticate()` with `threading.Lock`

**Code:**
```python
class DhanAuthProvider:
    def __init__(self, env_path: str = ".env", env: dict | None = None):
        self._env_path = env_path
        self._env = env
        self._tsl: Any = None
        self._refresh_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def authenticate(self) -> Any:
        """Run the authentication flow and return a connected Tradehull."""
        with self._lock:
            self._tsl = get_tradehull(env=self._env, env_path=self._env_path)
            self._schedule_proactive_refresh()
        return self._tsl

    def refresh_if_needed(self) -> Any:
        """Check if the current token is expired/near-expiry and refresh if needed."""
        if self._tsl is None:
            return self.authenticate()
        token = getattr(self._tsl, "token_id", None)
        if token:
            exp, _ = jwt_expiry(token)
            if exp is not None and int(time.time()) > (exp - EXPIRY_BUFFER_S):
                return self.authenticate()
        return self._tsl

    def _schedule_proactive_refresh(self) -> None:
        """Schedule a background timer to refresh the token before expiry."""
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

    def _proactive_refresh(self) -> None:
        """Timer callback: refresh, logging failures instead of raising."""
        try:
            self.authenticate()
            logger.info("proactive_token_refresh_ok: expiry=%.0fs",
                        self.time_until_expiry())
        except Exception as exc:
            logger.warning("proactive_token_refresh_failed: %s", exc)
```

### Layer 3: DhanBroker._ensure_tsl() in Critical Paths

**File:** `ntrade/brokers/dhan.py`

**Changes:**
- Added `_ensure_tsl()` method — mirrors testTrade's `token_manager.get_token()` pattern
- Injected into 6 critical methods: `get_quote()`, `place_order()`, `cancel_order()`, `modify_order()`, `get_balance()`, `get_positions()`
- Defensive: skips refresh if `_auth` doesn't exist (test mocks)
- Propagates new `tsl` to `DhanTransport` when refreshed

**Code:**
```python
class DhanBroker(BrokerAdapter):
    def _ensure_tsl(self):
        """Ensure the token is fresh before critical operations."""
        auth = getattr(self, "_auth", None)
        if auth is None:
            return self.tsl  # Test mock or uninitialized — skip refresh
        new_tsl = auth.refresh_if_needed()
        if new_tsl is not self.tsl:
            self.tsl = new_tsl
            if self._transport is not None:
                self._transport.tsl = new_tsl
        return self.tsl

    def get_quote(self, instrument: "Instrument", *, now: datetime | None = None) -> Quote:
        self._ensure_tsl()  # Ensure token is fresh before API call
        # ... rest of method ...

    def place_order(self, order: Order) -> Order:
        self._ensure_tsl()  # Ensure token is fresh before order placement
        # ... rest of method ...
```

---

## Token Lifecycle Flow

```
DhanBroker.get_quote()
  └── _ensure_tsl()
        └── DhanAuthProvider.refresh_if_needed()
              ├── token fresh? → return cached tsl
              ├── token expired/near-expiry? → authenticate()
              │     └── get_tradehull()
              │           ├── shared store token (with buffer check)
              │           ├── .env token (with buffer check)
              │           └── PIN+TOTP fallback (with cooldown)
              └── propagate new tsl to DhanTransport

Background Timer (proactive):
  └── _schedule_proactive_refresh()
        └── threading.Timer fires 15min before expiry
              └── _proactive_refresh()
                    └── authenticate() → fresh token
```

---

## Comparison: testTrade vs nTrade

| Aspect | testTrade | nTrade (after fix) |
|--------|-----------|-------------------|
| **Token source** | Direct REST call to Dhan auth API | Dhan-Tradehull SDK (PIN+TOTP) |
| **Token cache** | Daily file (`token_{client_id}_{date}.txt`) | Shared store (`~/.dhan/dhan-token-state.json`) |
| **Proactive refresh** | `threading.Timer` 15min before expiry | `threading.Timer` 15min before expiry ✅ |
| **Per-request check** | `token_manager.get_token()` on every HTTP call | `_ensure_tsl()` on critical operations ✅ |
| **Thread-safe** | `threading.Lock` in TokenManager | `threading.Lock` in DhanAuthProvider ✅ |
| **TOTP cooldown** | 125 seconds | 90 seconds |
| **Cross-process adoption** | Re-reads cache before minting | Shared store with sibling projects (Trade_XV2) |
| **Expiry buffer** | 900 seconds (15 min) | 900 seconds (15 min) ✅ |

**Key difference:** testTrade calls the Dhan REST API directly; nTrade wraps the Tradehull SDK. The SDK handles token renewal internally (which fails for APP tokens), so we must proactively avoid expired tokens rather than relying on renewal.

---

## Testing

### Test Results
- **Before fix:** Scary error output, fallback to PIN+TOTP worked but was confusing
- **After fix:** Clean output, silent fallback, all 616 tests passing

### Test Coverage
- `tests/test_dhan_auth_unit.py` — 10 tests covering JWT parsing, shared store, cooldown, fallback logic
- `tests/test_dhan_broker.py` — 5 tests covering broker methods with mocked Tradehull
- All existing tests pass with defensive `_ensure_tsl()` (skips refresh for test mocks)

### Manual Verification
```bash
$ python check_connection.py
Codebase Version 3.3.0
Attempting authentication using ACCESS TOKEN.
System is fetching the latest instrument file from Dhan
Already logged in for today, so reusing the token
Instrument file retrieved successfully
-----SUCCESSFULLY LOGGED INTO DHAN-----
[1/3] Fetching live market data (NIFTY LTP) ...
      NIFTY LTP = 24383.6
[2/3] Fetching account balance ...
      Available balance = 0.34

CONNECTION CHECK PASSED — Dhan Tradehull is working.
```

---

## Files Modified

1. **ntrade/brokers/dhan_auth.py**
   - Added `EXPIRY_BUFFER_S` constant
   - Proactive expiry check in `_token_from_shared_store()`
   - Buffer check for `.env` access token
   - Suppress Tradehull error output with `contextlib`

2. **ntrade/brokers/dhan_auth_provider.py**
   - Added `threading.Timer` for proactive background refresh
   - `refresh_if_needed()` — thread-safe freshness check
   - `time_until_expiry()` — seconds until token expires
   - `_schedule_proactive_refresh()` / `_proactive_refresh()`
   - `stop()` — cancel timer during shutdown

3. **ntrade/brokers/dhan.py**
   - Added `_ensure_tsl()` method
   - Injected into 6 critical methods: `get_quote()`, `place_order()`, `cancel_order()`, `modify_order()`, `get_balance()`, `get_positions()`
   - Defensive: skips refresh for test mocks

---

## Configuration

**Environment variables:**
- `DHAN_EXPIRY_BUFFER_S` — proactive refresh buffer (default: 900 seconds = 15 minutes)
- `DHAN_TOKEN_PATH` — shared token store path (default: `~/.dhan/dhan-token-state.json`)
- `DHAN_COOLDOWN_PATH` — TOTP cooldown file (default: `~/.dhan/dhan-totp-cooldown.json`)

---

## Kanban Board

**Task added:** T-011 — Auto-refresh Dhan token on expiry — proactive buffer + background timer + _ensure_tsl() in critical paths  
**Status:** ✅ Done  
**Tests:** 616 passing (0 failing)

---

## Lessons Learned

1. **Dhan APP tokens cannot be renewed** — the SDK's renewal attempt always fails with `DH-905`. We must proactively avoid expired tokens.
2. **One-shot auth is insufficient** — a 24-hour token lifetime means mid-session expiry is inevitable in long-running sessions.
3. **testTrade's pattern is production-proven** — mirroring their `TokenManager` + per-request `get_token()` pattern gives us the same reliability.
4. **Defensive design for testability** — `_ensure_tsl()` uses `getattr(self, "_auth", None)` to skip refresh for test mocks that bypass `__init__`.
5. **Suppress misleading errors** — Tradehull's "Token renew failed" output is confusing; we suppress it with `contextlib.redirect_stderr/stdout`.

---

## Future Enhancements (Optional)

1. **Direct REST mint** — bypass Tradehull and call Dhan's auth API directly (like testTrade) for full control
2. **Token metrics** — emit `TokenRefreshedEvent` to EventBus for observability
3. **Multi-broker token manager** — abstract `TokenManager` protocol for Upstox/ICICI integration
4. **Token cache encryption** — encrypt the shared token file at rest (currently 0o600 permissions)

---

## References

- testTrade TokenManager: `/Users/apple/Downloads/testTrade/scalpr/adapters/dhan/_auth.py`
- testTrade DhanHttpClient: `/Users/apple/Downloads/testTrade/scalpr/adapters/dhan/_http.py`
- testTrade Gateway: `/Users/apple/Downloads/testTrade/scalpr/gateway/__init__.py`
- nTrade auth flow: `ntrade/brokers/dhan_auth.py`
- nTrade broker: `ntrade/brokers/dhan.py`
- Kanban board: `.kanban/CONTEXT.md`
