---
kind: configuration_system
name: Environment-Based Configuration with Dotenv and Shared Token Store
category: configuration_system
scope:
    - '**'
source_files:
    - ntrade/brokers/dhan_auth.py
    - ntrade/brokers/dhan_auth_provider.py
    - ntrade/brokers/dhan.py
    - check_connection.py
    - pyproject.toml
---

The nTrade framework uses a minimal, environment-driven configuration system centered on `python-dotenv` for loading `.env` files and `os.environ` for runtime variables. There is no centralized config object or hierarchical settings loader — configuration is consumed directly at the point of use via environment variable lookups.

**Primary mechanism**: The `ntrade/brokers/dhan_auth.py` module provides the central `load_env(env_path=".env")` helper that calls `dotenv.load_dotenv(Path(env_path).absolute())`, and the `get_tradehull(env=None, env_path=".env")` function which reads all Dhan credentials from `os.environ` (or an injected `env` dict for testing). This pattern is mirrored by `DhanBroker.__init__` and `DhanAuthProvider.__init__`, both accepting `env_path` and `env` parameters to override defaults.

**Configuration sources (in precedence order)**:
1. **Shared token store** (`DHAN_TOKEN_PATH`): A JSON file containing a cached JWT access token, checked first. Tokens within `EXPIRY_BUFFER_S` (default 900s) of expiry are rejected proactively.
2. **`.env` access token** (`DHAN_ACCESS_TOKEN`): A raw JWT token loaded via dotenv, validated against proactive expiry before use.
3. **PIN+TOTP fallback** (`DHAN_PIN`, `DHAN_TOTP_SECRET`): Automatic re-authentication when tokens are missing/expired, respecting a shared cooldown file (`DHAN_COOLDOWN_PATH`) to avoid TOTP rate limits.

**Key environment variables**:
- `DHAN_CLIENT_ID` (required)
- `DHAN_ACCESS_TOKEN` (optional, JWT)
- `DHAN_PIN` / `DHAN_TOTP_SECRET` (fallback auth pair)
- `DHAN_TOKEN_PATH` / `DHAN_COOLDOWN_PATH` (shared state files, permissions set to 0o600)
- `DHAN_EXPIRY_BUFFER_S` (proactive refresh window, default 900)

**Design decisions**:
- No global config singleton — each component accepts `env_path`/`env` parameters, enabling per-instance overrides and test isolation.
- Secrets are never parsed into a typed config object; they are read lazily from `os.environ` at call time.
- File-based state (token cache, cooldown) uses JSON with restrictive file permissions (chmod 0o600).
- Optional dependency: `Dhan_Tradehull` is imported inside try/except so the package can be imported without it installed.
- Tests inject `env` dicts directly rather than manipulating `os.environ`, keeping tests deterministic.