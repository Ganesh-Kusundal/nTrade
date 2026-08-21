"""Tests pytest configuration.

Adds the repo root to ``sys.path`` so tests can import the top-level ``api``
package (``ntrade`` itself is importable via the editable install).

Re-exports canonical fixtures from ``tests.helpers`` — new code should
``from tests.helpers import make_dhan_broker, ohlcv, replay_kernel, ...``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Re-export helpers so they are importable via conftest and directly.
# This also ensures ``tests.helpers`` is importable in all test modules.
try:
    from tests.helpers import (  # noqa: F401
        INITIAL_CASH,
        MARKET_OPEN,
        FakeClock,
        FakeFeed,
        futures_master_csv,
        make_broker,
        make_dhan_broker,
        ohlcv,
        replay_kernel,
    )
except Exception:
    # helpers may not be importable during collection if ntrade deps missing;
    # keep conftest importable so pytest still starts.
    pass

import pytest

from tests.helpers import make_dhan_broker as _make_dhan_broker
from tests.helpers import ohlcv as _ohlcv
from tests.helpers import replay_kernel as _replay_kernel
from tests.helpers import MARKET_OPEN as _MARKET_OPEN
from tests.helpers import INITIAL_CASH as _INITIAL_CASH


@pytest.fixture
def dhan_broker():
    """Canonical DhanBroker stub (no network)."""
    return _make_dhan_broker()


@pytest.fixture
def ohlcv_fixture():
    """Deterministic OHLCV frame fixture."""
    return _ohlcv()


@pytest.fixture
def replay_kernel_fixture():
    """Replay TradingKernel fixture."""
    return _replay_kernel()


@pytest.fixture
def market_open():
    return _MARKET_OPEN


@pytest.fixture
def initial_cash():
    return _INITIAL_CASH
