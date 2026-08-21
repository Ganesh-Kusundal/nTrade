"""Bootstrap helpers for scripts — repo-root sys.path and broker factory."""

from __future__ import annotations

import sys
from pathlib import Path


def add_repo_root() -> None:
    """Ensure the repo root is on ``sys.path`` for ``ntrade`` / ``api`` imports."""
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def build_broker(name: str, env_path: str = ".env"):
    """
    Build a broker by name, delegating to ``BrokerRegistry`` with a manual fallback.

    Delegates to ``ntrade.registry.BrokerRegistry.get(name)``; if the registry
    is unavailable or the name is unknown, falls back to constructing
    ``PaperBroker`` / ``DhanBroker`` directly.
    """
    add_repo_root()
    key = name.strip().lower() if isinstance(name, str) else str(name).lower()
    # Primary path: registry
    try:
        from ntrade.registry import BrokerRegistry

        # BrokerRegistry factory supports **kwargs; try with env_path first for Dhan.
        try:
            return BrokerRegistry.get(key, env_path=env_path)
        except TypeError:
            # PaperBroker ignores env_path or factory signature mismatch
            return BrokerRegistry.get(key)
        except KeyError:
            # Unknown name — fall through to manual fallback below
            raise
    except KeyError:
        pass
    except Exception:
        # Registry import or construction failed — try manual fallback
        pass

    # Fallback: direct construction
    if key == "paper":
        from ntrade.brokers.paper import PaperBroker

        return PaperBroker()
    if key == "dhan":
        from ntrade.brokers.dhan import DhanBroker

        return DhanBroker(env_path=env_path)
    # Last attempt: try registry without env_path (may raise KeyError with helpful msg)
    try:
        from ntrade.registry import BrokerRegistry

        return BrokerRegistry.get(key)
    except Exception as exc:
        raise ValueError(f"unknown broker: {name!r}") from exc
