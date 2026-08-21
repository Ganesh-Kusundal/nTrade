"""Shared domain constants — no magic strings or magic numbers scattered in source.

Centralised so scanners, brokers, feeds, and runners all reference the same
values. When a default is also a constructor parameter, the parameter default
points here.
"""
from __future__ import annotations

import os
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


# ---- option-style taxonomy --------------------------------------------------
# Single home for the call/put flag. StrEnum so existing string comparisons
# (``option_type == "CE"``) keep working, while new code can compare against the
# enum member. Every option-type comparison in the codebase should reference
# these members instead of a bare literal.


class OptionType(StrEnum):
    """Option type — call (CE) or put (PE)."""

    CE = "CE"  # call
    PE = "PE"  # put


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


# ---- rounding precision -----------------------------------------------------
# Single home for decimal rounding so price/qty/rounding never drifts between
# the simulator, the broker executor, and the engines. Price fields round to
# 4 dp (paise); payoff / market-value fields round to 2 dp.
PRICE_PRECISION: int = 4
PAYOFF_PRECISION: int = 2


# ---- Dhan auth --------------------------------------------------------------
# JWT proactive-refresh buffer: refresh a token when it expires within this
# window (default 15 minutes) to avoid mid-session expiry. Overridable via
# the DHAN_EXPIRY_BUFFER_S env var.
JWT_EXPIRY_BUFFER_S: int = int(os.environ.get("DHAN_EXPIRY_BUFFER_S", "900"))


# ---- retry policy defaults --------------------------------------------------
RETRY_MAX_RETRIES: int = 3
RETRY_BASE_DELAY_S: float = 0.2
RETRY_MAX_DELAY_S: float = 5.0
RETRY_MULTIPLIER_S: float = 2.0
RETRY_JITTER_S: float = 0.1
