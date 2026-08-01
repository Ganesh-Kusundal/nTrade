---
kind: configuration_system
name: Environment-Based Configuration via python-dotenv
category: configuration_system
scope:
    - '**'
source_files:
    - ntrade/brokers/dhan_auth.py
    - ntrade/brokers/dhan.py
    - ntrade/facade.py
    - ntrade/kernel/trading_session.py
    - pyproject.toml
---

The nTrade framework uses a minimal, environment-variable-driven configuration system centered on `python-dotenv` and the OS environment. There is no centralized config object or typed settings model; instead, configuration is loaded lazily at connection time from `.env` files and environment variables.

**How it works**
- The single entry point for loading configuration is `ntrade.brokers.dhan_auth.load_env(env_path=".env")`, which calls `dotenv.load_dotenv(Path(env_path).absolute())`. This function is invoked inside `get_tradehull()`, which is the shared helper used by all Dhan authentication paths.
- Every component that needs credentials accepts an `env_path: str = ".env"` parameter (defaulting to a `.env` file in the current working directory) and optionally an `env: dict | None` override for testing. The same pattern appears on `DhanBroker.__init__`, `DhanAuthProvider.__init__`, `Market.__init__`, and `TradingSession.connect`.
- Configuration values are read directly from `os.environ` using `os.environ.get("VAR_NAME", default)` with sensible defaults. No schema validation or type coercion beyond basic string stripping and `int()` conversion is applied.

**Configuration sources and precedence**
1. **Shared token store** (`DHAN_TOKEN_PATH`): A JSON file containing a cached access token with expiry metadata. If present and not within the proactive expiry buffer, it is used first.
2. **`.env` access token** (`DHAN_ACCESS_TOKEN`): Used if present and not expired (proactive buffer applied).
3. **PIN+TOTP fallback** (`DHAN_PIN`, `DHAN_TOTP_SECRET`): Automatic fallback when tokens are missing/expired, respecting a cooldown file (`DHAN_COOLDOWN_PATH`).
4. **OS environment**: All of the above can be supplied via actual environment variables, making Docker/container deployment straightforward.

**Key environment variables**
- `DHAN_CLIENT_ID` — required client code (raises `ValueError` if missing)
- `DHAN_ACCESS_TOKEN` — JWT access token (optional, validated for expiry)
- `DHAN_AUTH_MODE` — authentication mode hint
- `DHAN_PIN` — PIN for TOTP-based login (optional)
- `DHAN_TOTP_SECRET` — TOTP secret for PIN+TOTP flow (optional)
- `DHAN_TOKEN_PATH` — path to shared token store JSON file (optional)
- `DHAN_COOLDOWN_PATH` — path to TOTP attempt cooldown file (optional)
- `DHAN_EXPIRY_BUFFER_S` — proactive token expiry buffer in seconds (default: 900)
- `TZ` — timezone setting used in tests and candle processing

**Design characteristics**
- **No global config singleton**: Each module reads from `os.environ` directly, keeping dependencies explicit through function parameters.
- **Testability**: The `env: dict | None` parameter allows injecting a fake environment in unit tests without touching `os.environ`.
- **Lazy loading**: `.env` files are only loaded when `get_tradehull()` is called, not at import time.
- **No configuration file formats**: No YAML, TOML, or JSON config files are parsed by the framework itself; only the `.env` format via `python-dotenv` and the shared token store JSON (which is managed internally, not user-configured).
- **Hardcoded defaults**: Defaults are embedded as Python literals (e.g., `EXPIRY_BUFFER_S = int(os.environ.get("DHAN_EXPIRY_BUFFER_S", "900"))`) rather than being defined in a central location.