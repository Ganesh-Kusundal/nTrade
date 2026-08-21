"""
Shared test fixtures — single source of truth.
Import from here instead of redefining per-file.

New code should import from ``tests.helpers`` (canonical). Per-file helper
definitions are deprecated; they are kept only for backward compat until
the 107 test files are refactored to use this module.

Deprecation note:
    Do not copy-paste helpers into new test files. Import from here:
    ``from tests.helpers import make_dhan_broker, ohlcv, replay_kernel, ...``
    Existing per-file definitions should be removed in a follow-up refactor.
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ntrade.brokers.dhan import DhanBroker
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARKET_OPEN = datetime(2026, 1, 1, 9, 15)
INITIAL_CASH = 100_000.0

# ---------------------------------------------------------------------------
# Dhan broker stub
# ---------------------------------------------------------------------------


def make_dhan_broker(**tsl_methods):
    """
    Canonical DhanBroker stub for unit tests (no live network).

    Mirrors ``tests/test_dhan_broker.py:18-23`` — the most complete version.
    """
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(**tsl_methods)
    broker._transport = DhanTransport(broker.tsl)
    return broker


# Backward-compat alias: some older tests import ``make_broker``.
def make_broker(**tsl_methods):
    return make_dhan_broker(**tsl_methods)


# ---------------------------------------------------------------------------
# OHLCV helpers (triple-identical across test_candle_engine / test_statutory_wiring / test_replay_backtest)
# ---------------------------------------------------------------------------


def ohlcv(bars: int = 20, step_price: float = 1.0) -> pd.DataFrame:
    """
    Deterministic OHLCV frame starting 2026-01-01 09:15.
    open  = 100 + i*step, high = 102 + i*step, low = 99 + i*step,
    close = 101 + i*step, volume = 1000, 5-minute bars.
    """
    start = datetime(2026, 1, 1, 9, 15)
    rows = []
    for i in range(bars):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=5 * i),
                "open": 100 + i * step_price,
                "high": 102 + i * step_price,
                "low": 99 + i * step_price,
                "close": 101 + i * step_price,
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Kernel helpers
# ---------------------------------------------------------------------------


def replay_kernel(timeframe: str = "1m") -> TradingKernel:
    """Replay-mode kernel with a deterministic ReplayClock."""
    return TradingKernel(mode="replay", clock=ReplayClock(), timeframe=timeframe)


# ---------------------------------------------------------------------------
# Deterministic clock (from tests/test_rate_limit.py:19-31)
# ---------------------------------------------------------------------------


class FakeClock:
    """Deterministic clock + sleep pair: sleep() advances the clock."""

    def __init__(self, t: float = 0.0):
        self.t = t
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


# ---------------------------------------------------------------------------
# Fake feed (from tests/test_dhan_feed_source.py:149-165)
# ---------------------------------------------------------------------------


class FakeFeed:
    """A stand-in for dhanhq.MarketFeed that records the callback wiring."""

    def __init__(self, subscriptions):
        self.subscriptions = subscriptions
        self.started = False
        self.closed = False
        self.handler = None
        self.thread = None

    def start(self):
        self.started = True
        self.thread = object()  # like dhanhq returns a Thread
        return self.thread

    def close_connection(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Instrument master CSV (extracted from tests/test_api_market.py _sample_master)
# ---------------------------------------------------------------------------


def futures_master_csv(tmp_path: Path) -> Path:
    """
    Write a 16-col SEM_* instrument master CSV with sample futures rows.

    Returns the Path to the written CSV (``tmp_path / "master.csv"``).
    Mirrors ``tests/test_api_market.py::_sample_master``.
    """
    header = (
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
        "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
        "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
        "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME"
    )
    rows = [
        "NSE,D,58072,FUTIDX,0,NIFTY-Aug2026-FUT,65.0,NIFTY AUG FUT,"
        "2026-08-25 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY",
        "NSE,D,58073,FUTIDX,0,NIFTY-Sep2026-FUT,65.0,NIFTY SEP FUT,"
        "2026-09-29 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY",
        "NSE,D,58074,FUTIDX,0,NIFTY-Oct2026-FUT,75.0,NIFTY OCT FUT,"
        "2026-10-27 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY",
        "NSE,D,58067,FUTIDX,0,BANKNIFTY-Aug2026-FUT,30.0,BANKNIFTY AUG FUT,"
        "2026-08-25 14:30:00,-0.01,XX,20.0,M,FUT,,BANKNIFTY",
        "MCX,M,449735,FUTCOM,0,CRUDEOIL-19Aug2026-FUT,1.0,CRUDEOIL AUG FUT,"
        "2026-08-19 23:30:00,-0.01,XX,1.0,M,FUTCOM,,CRUDEOIL",
        "NSE,D,58066,FUTIDX,0,FINNIFTY-Aug2026-FUT,40.0,FINNIFTY AUG FUT,"
        "2026-08-25 14:30:00,-0.01,XX,5.0,M,FUT,,FINNIFTY",
    ]
    path = Path(tmp_path) / "master.csv"
    # Preserve the leading-comma quirk from the original _sample_master so
    # FuturesMaster's csv.reader header scan (r[1] == "SEM_EXM_EXCH_ID") keeps passing.
    path.write_text("\n".join(["," + header] + ["," + r for r in rows]), encoding="utf-8")
    return path


__all__ = [
    "MARKET_OPEN",
    "INITIAL_CASH",
    "make_dhan_broker",
    "make_broker",
    "ohlcv",
    "replay_kernel",
    "FakeClock",
    "FakeFeed",
    "futures_master_csv",
]
