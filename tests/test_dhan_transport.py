"""Tests for DhanTransport's BrokerRateGate choke point (T-026).

Every outbound ``self._tsl.*`` call must pass through ``_invoke(quota, fn)``,
and rate-limit failures must surface as ``RateLimited`` — never as empty/None
success.
"""

from __future__ import annotations

import pandas as pd
import pytest
from unittest.mock import MagicMock

from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.execution.rate_limit import (
    BrokerRateGate, Quota, RateLimited,
)


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _transport_with_gate(tsl, *, rate: float | None = None):
    """Transport with a fake-clock gate (fast window for deterministic tests)."""
    clock = FakeClock()
    gate = BrokerRateGate(clock=clock, sleep=clock.sleep)
    if rate is not None:
        from ntrade.execution.rate_limit import DEFAULT_WINDOWS
        windows = dict(DEFAULT_WINDOWS)
        windows[Quota.QUOTE] = ((rate, 1),)
        gate = BrokerRateGate(clock=clock, sleep=clock.sleep, windows=windows)
    return DhanTransport(tsl, gate=gate), clock, gate


class TestGateAcquisition:
    def test_quote_acquire_blocks_second_call_within_1s(self):
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"TCS": 100.0}
        transport, clock, _ = _transport_with_gate(tsl, rate=1.0)
        assert transport.get_ltp("TCS") == 100.0
        clock.t = 0.5
        transport.get_ltp("TCS")   # must block until t >= 1.0
        assert clock.t >= 1.0

    def test_order_path_acquires_order_quota(self):
        tsl = MagicMock()
        tsl.order_placement.return_value = "O1"
        transport, clock, gate = _transport_with_gate(tsl)
        transport.place_order(tradingsymbol="TCS", exchange="NSE")
        assert len(gate._history[Quota.ORDER][0]) == 1
        assert len(gate._history[Quota.QUOTE][0]) == 0

    def test_history_acquires_data_quota(self):
        tsl = MagicMock()
        tsl.get_historical_data.return_value = pd.DataFrame({
            "Timestamp": ["2026-08-01 09:15:00"],
            "Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5],
            "Volume": [10],
        })
        transport, clock, gate = _transport_with_gate(tsl)
        transport.get_historical("NIFTY", "NSE", "5m")
        assert len(gate._history[Quota.DATA][0]) == 1

    def test_history_acquires_data_quota_assert_window_shape(self):
        """Data class carries the 5/s + 100k/day window shape."""
        gate = BrokerRateGate()
        spans = [span for span, _ in gate._windows[Quota.DATA]]
        limits = [limit for _, limit in gate._windows[Quota.DATA]]
        assert spans == [1.0, 86400.0]
        assert limits == [5, 100_000]


class TestRateLimitedPropagation:
    def test_history_raises_rate_limited_on_dh904(self):
        tsl = MagicMock()
        tsl.get_historical_data.side_effect = RuntimeError("DH-904 rate limit")
        transport, _, _ = _transport_with_gate(tsl)
        with pytest.raises(RateLimited):
            transport.get_historical("NIFTY", "NSE", "5m")

    def test_ohlc_raises_rate_limited_not_empty_dict(self):
        tsl = MagicMock()
        tsl.get_ohlc_data.side_effect = RateLimited(Quota.QUOTE, message="DH-904")
        transport, _, _ = _transport_with_gate(tsl)
        with pytest.raises(RateLimited):
            transport.get_ohlc("NIFTY")

    def test_expiry_list_raises_rate_limited_not_empty(self):
        tsl = MagicMock()
        tsl.get_expiry_list.side_effect = RuntimeError("Rate_Limit exceeded")
        transport, _, _ = _transport_with_gate(tsl)
        with pytest.raises(RateLimited):
            transport.get_expiry_list("NIFTY", "NSE")

    def test_non_rate_limit_history_keeps_empty_fallback(self):
        """A plain transient error (not DH-904) still degrades to empty."""
        tsl = MagicMock()
        tsl.get_historical_data.side_effect = ConnectionError("network down")
        transport, _, _ = _transport_with_gate(tsl)
        series = transport.get_historical("NIFTY", "NSE", "5m")
        assert series is not None and series.empty

    def test_dh904_penalizes_the_class(self):
        """A DH-904 must back the class off via gate.penalize (not generic retry)."""
        tsl = MagicMock()
        tsl.get_historical_data.side_effect = RateLimited(Quota.DATA, retry_after=2.0)
        transport, clock, gate = _transport_with_gate(tsl)
        with pytest.raises(RateLimited):
            transport.get_historical("NIFTY", "NSE", "5m")
        assert gate._cooldown_until[Quota.DATA] >= clock.t + 2.0


class TestNoLimiterLegacy:
    def test_rate_limiter_param_is_gone(self):
        """The old 10/s LTP-only limiter path is removed — no `rate_limiter` param."""
        from ntrade.brokers.dhan_transport import DhanTransport
        with pytest.raises(TypeError):
            DhanTransport(MagicMock(), rate_limiter=MagicMock())
