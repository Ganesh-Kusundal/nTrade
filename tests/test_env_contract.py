"""The env contract: every documented variable is real, every dead one is
gone. DHAN_AUTH_MODE=STATIC and DHAN_REFRESH_BUFFER_MINUTES were read by no
code — an operator believing STATIC disables TOTP minting is a safety hazard.
"""
from pathlib import Path


def test_dead_env_keys_are_gone():
    env = Path(".env")
    if not env.exists():
        return  # CI may not carry a local .env; .env.example is the contract
    text = env.read_text()
    for dead in ("DHAN_AUTH_MODE", "DHAN_REFRESH_BUFFER_MINUTES"):
        assert dead not in text, f"{dead} is read by no code — remove it from .env"


def test_env_example_documents_real_keys():
    text = Path(".env.example").read_text()
    for key in ("DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "DHAN_TOKEN_PATH",
                "DHAN_EXPIRY_BUFFER_S", "NTRADE_MARKET_PROVIDER",
                "NTRADE_EVENT_STORE"):
        assert key in text, f".env.example must document {key}"
    for dead in ("DHAN_AUTH_MODE", "DHAN_REFRESH_BUFFER_MINUTES"):
        assert dead not in text
