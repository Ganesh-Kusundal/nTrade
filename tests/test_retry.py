"""Tests for RetryPolicy, RateLimiter, and DhanTransport retry integration."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from ntrade.execution.retry import RateLimiter, RetryPolicy


# ================================================================ RetryPolicy


class TestRetryPolicy:
    def test_defaults(self):
        p = RetryPolicy()
        assert p.max_retries == 3
        assert p.base_delay == 0.2
        assert p.max_delay == 5.0
        assert p.multiplier == 2.0
        assert p.jitter == 0.1

    def test_frozen(self):
        p = RetryPolicy()
        with pytest.raises(AttributeError):
            p.max_retries = 5  # type: ignore[misc]

    def test_success_on_first_try(self):
        policy = RetryPolicy(max_retries=3, base_delay=0.0, jitter=0.0)
        fn = MagicMock(return_value=42)
        result = policy.execute(fn, "arg", key="val")
        assert result == 42
        assert fn.call_count == 1
        fn.assert_called_with("arg", key="val")

    def test_success_after_retries(self):
        policy = RetryPolicy(max_retries=3, base_delay=0.0, jitter=0.0)
        fn = MagicMock(side_effect=[RuntimeError("boom"), RuntimeError("boom"), "ok"])
        result = policy.execute(fn)
        assert result == "ok"
        assert fn.call_count == 3

    def test_exhaustion_raises_last_exception(self):
        policy = RetryPolicy(max_retries=3, base_delay=0.0, jitter=0.0)
        fn = MagicMock(side_effect=ValueError("always fails"))
        with pytest.raises(ValueError, match="always fails"):
            policy.execute(fn)
        assert fn.call_count == 3

    def test_single_retry(self):
        """max_retries=1 means a single attempt, no retries."""
        policy = RetryPolicy(max_retries=1, base_delay=0.0, jitter=0.0)
        fn = MagicMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError, match="fail"):
            policy.execute(fn)
        assert fn.call_count == 1

    def test_passes_args_and_kwargs(self):
        policy = RetryPolicy(max_retries=1, base_delay=0.0, jitter=0.0)
        fn = MagicMock(return_value="result")
        policy.execute(fn, 1, 2, x=10, y=20)
        fn.assert_called_once_with(1, 2, x=10, y=20)


class TestRetryPolicyDelays:
    def test_exponential_backoff(self):
        policy = RetryPolicy(
            max_retries=4, base_delay=1.0, multiplier=2.0,
            max_delay=100.0, jitter=0.0,
        )
        delays = list(policy.delays())
        assert len(delays) == 4
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(4.0)
        assert delays[3] == pytest.approx(8.0)

    def test_max_delay_cap(self):
        policy = RetryPolicy(
            max_retries=5, base_delay=1.0, multiplier=2.0,
            max_delay=3.0, jitter=0.0,
        )
        delays = list(policy.delays())
        # 1, 2, 3 (capped), 3 (capped), 3 (capped)
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(3.0)
        assert delays[3] == pytest.approx(3.0)
        assert delays[4] == pytest.approx(3.0)

    def test_jitter_within_range(self):
        policy = RetryPolicy(
            max_retries=100, base_delay=1.0, multiplier=1.0,
            max_delay=100.0, jitter=0.2,
        )
        delays = list(policy.delays())
        for d in delays:
            assert 0.9 <= d <= 1.1

    def test_delays_never_negative(self):
        """Even with extreme jitter, delays are clamped to >= 0."""
        policy = RetryPolicy(
            max_retries=10, base_delay=0.01, multiplier=1.0,
            max_delay=1.0, jitter=100.0,
        )
        delays = list(policy.delays())
        for d in delays:
            assert d >= 0.0

    @patch("ntrade.execution.retry.time.sleep")
    def test_execute_sleeps_between_retries(self, mock_sleep):
        policy = RetryPolicy(
            max_retries=3, base_delay=0.5, multiplier=2.0,
            max_delay=10.0, jitter=0.0,
        )
        fn = MagicMock(side_effect=[RuntimeError("a"), RuntimeError("b"), "ok"])
        result = policy.execute(fn)
        assert result == "ok"
        assert mock_sleep.call_count == 2  # sleep between attempts, not after last
        mock_sleep.assert_any_call(pytest.approx(0.5))
        mock_sleep.assert_any_call(pytest.approx(1.0))


# ================================================================ RateLimiter


class TestRateLimiter:
    def test_first_call_is_immediate(self):
        limiter = RateLimiter(calls_per_second=100)
        t0 = time.monotonic()
        limiter.wait()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05

    def test_rate_limiting_enforced(self):
        """20 calls/sec → 0.05s interval.  5 calls should take >= 0.2s."""
        limiter = RateLimiter(calls_per_second=20)
        t0 = time.monotonic()
        for _ in range(5):
            limiter.wait()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.15  # allow small timing tolerance

    def test_thread_safety(self):
        """Concurrent access must not corrupt internal state."""
        limiter = RateLimiter(calls_per_second=1000)
        counter = {"ok": 0}
        lock = threading.Lock()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(10):
                    limiter.wait()
                with lock:
                    counter["ok"] += 1
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors
        assert counter["ok"] == 4  # all 4 threads completed 10 waits each


# ================================================================ Integration


class TestDhanTransportRetryIntegration:
    def test_default_policy_used(self):
        """DhanTransport creates a default RetryPolicy when none is provided."""
        from ntrade.brokers.dhan_transport import DhanTransport
        tsl = MagicMock()
        transport = DhanTransport(tsl)
        assert isinstance(transport._retry_policy, RetryPolicy)
        assert transport._retry_policy.max_retries == 3
        assert transport._retry_policy.base_delay == 0.2

    def test_custom_policy_accepted(self):
        """DhanTransport accepts a custom RetryPolicy."""
        from ntrade.brokers.dhan_transport import DhanTransport
        policy = RetryPolicy(max_retries=5, base_delay=0.1)
        tsl = MagicMock()
        transport = DhanTransport(tsl, retry_policy=policy)
        assert transport._retry_policy is policy
        assert transport._retry_policy.max_retries == 5

    @patch("ntrade.execution.retry.time.sleep")
    def test_get_ltp_uses_retry_policy(self, mock_sleep):
        """get_ltp delegates to the retry policy (not hardcoded loop)."""
        from ntrade.brokers.dhan_transport import DhanTransport
        tsl = MagicMock()
        tsl.get_ltp_data.side_effect = [
            {"REL": None},     # attempt 1 → LTP is 0 → retry
            {"REL": None},     # attempt 2 → LTP is 0 → retry
            {"REL": 2500.0},   # attempt 3 → success
        ]
        policy = RetryPolicy(max_retries=3, base_delay=0.0, jitter=0.0)
        transport = DhanTransport(tsl, retry_policy=policy)
        result = transport.get_ltp("REL")
        assert result == 2500.0
        assert tsl.get_ltp_data.call_count == 3

    def test_get_ltp_raises_on_exhaustion(self):
        """When all retries fail, get_ltp raises BrokerDataError (never 0.0)."""
        from ntrade.brokers.dhan_transport import BrokerDataError, DhanTransport
        tsl = MagicMock()
        tsl.get_ltp_data.side_effect = Exception("network down")
        policy = RetryPolicy(max_retries=3, base_delay=0.0, jitter=0.0)
        transport = DhanTransport(tsl, retry_policy=policy)
        with pytest.raises(BrokerDataError):
            transport.get_ltp("REL")
        assert tsl.get_ltp_data.call_count == 3

    def test_get_ltp_success_first_try(self):
        """Happy path: no retries needed."""
        from ntrade.brokers.dhan_transport import DhanTransport
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"REL": 100.0}
        policy = RetryPolicy(max_retries=3, base_delay=0.0, jitter=0.0)
        transport = DhanTransport(tsl, retry_policy=policy)
        assert transport.get_ltp("REL") == 100.0
        assert tsl.get_ltp_data.call_count == 1

    def test_existing_constructor_still_works(self):
        """Backward compat: DhanTransport(tsl) without retry_policy still works."""
        from ntrade.brokers.dhan_transport import DhanTransport
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"SYM": 50.0}
        transport = DhanTransport(tsl)
        assert transport.get_ltp("SYM") == 50.0
