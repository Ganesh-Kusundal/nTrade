---
kind: configuration_system
name: Environment-Based Configuration with .env and Shared Token Store
category: configuration_system
scope:
    - '**'
source_files:
    - ntrade/brokers/dhan_auth.py
    - ntrade/brokers/dhan.py
    - ntrade/facade.py
    - ntrade/kernel/trading_session.py
    - check_connection.py
---

The ntrade framework uses a lightweight, environment-variable-driven configuration system centered around `.env` files loaded via `python-dotenv`, combined with a shared JSON token store for broker authentication state. There is no centralized config object or typed settings module — configuration is consumed directly from `os.environ` at the point of use.

**Primary mechanism: `.env` file loading**
The single entry point for loading configuration is `ntrade/brokers/dhan_auth.py::load_env(env_path)`, which calls `dotenv.load_dotenv(Path(env_path).absolute())`. This function is invoked inside `get_tradehull()`, the central factory that creates a connected Dhan Tradehull client. Every component that needs credentials passes an `env_path` parameter (defaulting to `.env`) down through the call chain: `TradingSession.connect()` → `BrokerRegistry.get()` → `DhanBroker.__init__()` → `DhanAuthProvider.authenticate()` → `dhan_auth.get_tradehull()`.

**Configuration variables**
The system reads these environment variables:
- `DHAN_CLIENT_ID` — required; raises `ValueError` if missing
- `DHAN_ACCESS_TOKEN` — optional bootstrap seed; promoted to shared store on success
- `DHAN_PIN` / `DHAN_TOTP_SECRET` — optional; used as fallback when cached token is unavailable
- `DHAN_TOKEN_PATH` — path to shared JSON token cache file
- `DHAN_COOLDOWN_PATH` — path to TOTP attempt cooldown state file
- `DHAN_EXPIRY_BUFFER_S` — proactive expiry buffer in seconds (default 900)
- `TZ` — timezone variable used by tests and candle processing

**Shared token store strategy**
Authentication follows a strict three-tier priority implemented in `get_tradehull()`:
1. **Shared store** (`DHAN_TOKEN_PATH`): Load persisted JWT if present and not near expiry (within `EXPIRY_BUFFER_S`)
2. **Env seed** (`DHAN_ACCESS_TOKEN`): Use as bootstrap only; promote to store on successful login
3. **PIN+TOTP mint**: Fall back to interactive PIN+TOTP flow if both are provided and cooldown allows

Every successful path persists the working token into the shared store as a JSON file with fields `access_token`, `expires_at`, `expires_at_ms`, and `source`. Files are created with restrictive permissions (`0o600`). Dead tokens are automatically cleared before fallthrough.

**Runtime tuning via environment**
Beyond credentials, runtime behavior is controlled through environment variables read directly from `os.environ`: `DHAN_EXPIRY_BUFFER_S` controls proactive token refresh timing, and `TZ` affects time handling in tests.

**No global config object**
The codebase deliberately avoids a centralized configuration registry. Each module reads what it needs from `os.environ` at import/runtime. The `.env` file is the sole persistent configuration source; there are no YAML, TOML, or JSON config files for application settings. Broker-specific configuration lives entirely in the `.env` file and the shared token store JSON.