"""Shared domain constants — no magic strings or magic numbers scattered in source.

Centralised so scanners, brokers, feeds, and runners all reference the same
values. When a default is also a constructor parameter, the parameter default
points here.
"""
from __future__ import annotations

from enum import StrEnum


# ---- exchanges -------------------------------------------------------------

class Exchange(StrEnum):
    """NSE exchange segments used across brokers, feeds, and domain objects."""

    CASH = "NSE"        # NSE cash / equity
    BSE = "BSE"          # BSE cash
    MCX = "MCX"          # Multi Commodity Exchange
    DERIVATIVES = "NFO"  # NSE Futures & Options
    BFO = "BFO"          # BSE Futures & Options
    CURRENCY = "CUR"     # Currency derivatives (NSE)
    INDEX = "INDEX"      # index-level instruments (Dhan internal)


# Exchanges that trade derivatives (used for SEBI staleness, day-block logic,
# futures carry, etc.)
DERIVATIVE_EXCHANGES: frozenset[str] = frozenset({
    Exchange.DERIVATIVES, Exchange.BFO, Exchange.CURRENCY, Exchange.MCX,
})

# Dhan SEBI staleness applies to F&O exchanges only
SEBI_FNO_EXCHANGES: frozenset[str] = frozenset({
    Exchange.DERIVATIVES, Exchange.BFO,
})


# ---- timeframes ------------------------------------------------------------

class Timeframe(StrEnum):
    """Canonical timeframe strings shared across engines, brokers, and feeds."""

    S1 = "1s"
    S5 = "5s"
    MIN = "1m"
    T5 = "5m"
    T15 = "15m"
    H1 = "1h"
    D1 = "1d"


DEFAULT_TIMEFRAME = Timeframe.MIN


# ---- throttling & intervals ------------------------------------------------
# Shared by scanner rate-limits, circuit-breaker cooldowns, and kill-switch
# backoffs — all use the same 30s cadence to avoid magic-number drift.

SCAN_THROTTLE_S: float = 30.0
HEARTBEAT_INTERVAL_S: float = 30.0
RATE_LIMIT_RETRY_S: float = 1.0

# Circuit-break defaults (also used by BrokerExecution config)
CIRCUIT_FAILURE_THRESHOLD: int = 15
CIRCUIT_COOLDOWN_S: float = SCAN_THROTTLE_S  # reuse the 30s cadence

# Runner polling defaults
POLL_INTERVAL_S: float = 5.0
SYNC_INTERVAL_S: float = 60.0
WARMUP_TIMEOUT_S: float = 15.0
FEED_WATCHDOG_TIMEOUT_S: float = 30.0

# Dhan TOTP cooldown (broker-enforced lockout)
SEBI_TOTP_COOLDOWN_S: float = 120.0
SEBI_MAX_QUOTE_AGE_S: float = 10.0
SEBI_BAND_PCT: float = 2.0

# Market-data staleness (Quote.is_stale / History.is_fresh / Capabilities.is_stale)
QUOTE_MAX_AGE_S: float = 5.0
HISTORY_MAX_AGE_MIN: float = 5.0

# Cost defaults
DEFAULT_RISK_FREE_RATE: float = 0.065  # 6.5% annual — approximate Indian risk-free
DEFAULT_INITIAL_CASH: float = 100_000.0  # Default opening cash for paper trading, backtests, and sessions (₹1 lakh)
