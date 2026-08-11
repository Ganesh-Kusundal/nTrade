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

    def test_non_rate_limit_history_raises_not_empty(self):
        """B-014: a plain transient error (not DH-904) must RAISE instead of
        silently returning an empty CandleSeries — a strategy has to tell
        "no bars" apart from "the broker call failed"."""
        from ntrade.brokers.dhan_transport import BrokerDataError
        tsl = MagicMock()
        tsl.get_historical_data.side_effect = ConnectionError("network down")
        transport, _, _ = _transport_with_gate(tsl)
        with pytest.raises(BrokerDataError):
            transport.get_historical("NIFTY", "NSE", "5m")

    def test_dh904_penalizes_the_class(self):
        """A DH-904 must back the class off via gate.penalize (not generic retry)."""
        tsl = MagicMock()
        tsl.get_historical_data.side_effect = RateLimited(Quota.DATA, retry_after=2.0)
        transport, clock, gate = _transport_with_gate(tsl)
        with pytest.raises(RateLimited):
            transport.get_historical("NIFTY", "NSE", "5m")
        assert gate._cooldown_until[Quota.DATA] >= clock.t + 2.0


class TestResampleTimeframes:
    def test_sub_5m_fetches_1m_base_and_resamples(self):
        """F-001: 3m is served by fetching the 1m base and resampling — the
        backend never sees a "3" interval."""
        tsl = MagicMock()
        tsl.get_historical_data.return_value = pd.DataFrame({
            "Timestamp": [
                "2026-08-03 09:15:00", "2026-08-03 09:16:00", "2026-08-03 09:17:00",
                "2026-08-03 09:18:00", "2026-08-03 09:19:00", "2026-08-03 09:20:00",
            ],
            "Open": [10, 11, 12, 13, 14, 15],
            "High": [11, 12, 13, 14, 15, 16],
            "Low": [9, 10, 11, 12, 13, 14],
            "Close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5],
            "Volume": [100, 100, 100, 100, 100, 100],
        })
        transport, _, _ = _transport_with_gate(tsl)
        series = transport.get_historical("NIFTY", "NSE", "3m")
        # The library call must use the 1m base interval, never "3"
        assert tsl.get_historical_data.call_args.kwargs["timeframe"] == "1"
        # 6 one-minute rows -> 2 three-minute candles
        assert len(series) == 2
        df = series.to_dataframe()
        assert df["open"].iloc[0] == 10      # first of the bucket
        assert df["close"].iloc[-1] == 15.5  # last of the last bucket
        assert df["high"].max() == 16
        assert df["low"].min() == 9
        assert df["volume"].iloc[0] == 300   # summed
        assert series.timeframe == "3m"

    def test_resample_history_labels_match_engine_right_edge(self):
        """F-001 parity with CandleEngine (K-025): resample_history must label
        bars at the bin's RIGHT edge (09:18/09:21 for 3m bins starting
        09:15/09:18) — not pandas' default bin-START labels (09:15/09:18). Bin
        membership (closed="left") and the 09:15 IST origin stay unchanged.

        CandleEngine has no native 3m interval (3m exists only via this F-001
        path), so the expected labels mirror its bucketing formula directly:
        naive ts pinned to UTC, floor to ``seconds``, label = bucket + seconds.
        """
        from datetime import datetime, timedelta, timezone
        from ntrade.brokers.dhan_mapper import DhanMapper

        def _engine_label(t: datetime) -> datetime:
            seconds = 180  # 3m, as CandleEngine would compute for this rule
            epoch = int(t.replace(tzinfo=timezone.utc).timestamp())
            bucket = epoch - (epoch % seconds)
            return datetime.fromtimestamp(bucket + seconds, tz=timezone.utc).replace(tzinfo=None)

        start = datetime(2026, 8, 3, 9, 15)  # naive IST wall clock
        ts = [start + timedelta(minutes=i) for i in range(6)]  # 09:15..09:20
        df = pd.DataFrame({
            "timestamp": ts,
            "open": [10 + i for i in range(6)],
            "high": [11 + i for i in range(6)],
            "low": [9 + i for i in range(6)],
            "close": [10.5 + i for i in range(6)],
            "volume": [100] * 6,
        })
        out = DhanMapper.resample_history(df, "3min")
        got = list(pd.to_datetime(out["timestamp"]))
        engine_labels = [_engine_label(start), _engine_label(start + timedelta(minutes=3))]
        assert got == engine_labels, f"resample_history labels {got} != engine end-of-bar {engine_labels}"
        # Membership unchanged: two 3-bar bins, same OHLCV aggregation.
        assert len(out) == 2
        assert out["open"].iloc[0] == 10 and out["close"].iloc[0] == 12.5
        assert out["open"].iloc[1] == 13 and out["close"].iloc[1] == 15.5

    def test_resample_history_night_session_keeps_day_grouping_right_edge(self):
        """F-001: a night-session candle keeps its right-edge label AND stays
        inside its own calendar day — the 09:15 IST origin axis is untouched by
        the K-025 label change."""
        from ntrade.brokers.dhan_mapper import DhanMapper
        df = pd.DataFrame({
            "timestamp": [
                pd.Timestamp("2026-08-03 15:27:00"),  # night session, day 1
                pd.Timestamp("2026-08-03 15:28:00"),
                pd.Timestamp("2026-08-04 09:15:00"),  # next session, day 2
                pd.Timestamp("2026-08-04 09:16:00"),
            ],
            "open": [1, 2, 3, 4], "high": [2, 3, 4, 5],
            "low": [0, 1, 2, 3], "close": [1.5, 2.5, 3.5, 4.5],
            "volume": [10] * 4,
        })
        out = DhanMapper.resample_history(df, "3min")
        # Day 1: 15:27/15:28 -> bin [15:27, 15:30) labeled 15:30 (right edge)
        # Day 2: 09:15/09:16 -> bin [09:15, 09:18) labeled 09:18
        labels = list(pd.to_datetime(out["timestamp"]))
        assert labels == [pd.Timestamp("2026-08-03 15:30:00"),
                          pd.Timestamp("2026-08-04 09:18:00")], f"got {labels}"
        assert len(out) == 2

    def test_native_timeframe_never_resampled(self):
        """5m/15m etc. pass straight through with no resample call."""
        tsl = MagicMock()
        tsl.get_historical_data.return_value = pd.DataFrame({
            "Timestamp": ["2026-08-03 09:15:00"],
            "Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10],
        })
        transport, _, _ = _transport_with_gate(tsl)
        series = transport.get_historical("NIFTY", "NSE", "5m")
        assert tsl.get_historical_data.call_args.kwargs["timeframe"] == "5"
        assert len(series) == 1

    def test_map_timeframe_sub5m_maps_to_base_and_others_raise(self):
        """B-013: 2m/3m/4m map to the 1m base; truly unsupported tfs raise."""
        from ntrade.brokers.dhan_mapper import DhanMapper
        assert DhanMapper.map_timeframe("3m") == "1"
        assert DhanMapper.resample_rule("3m") == "3min"
        assert DhanMapper.resample_rule("5m") is None
        with pytest.raises(ValueError):
            DhanMapper.map_timeframe("10m")


class TestNoLimiterLegacy:
    def test_rate_limiter_param_is_gone(self):
        """The old 10/s LTP-only limiter path is removed — no `rate_limiter` param."""
        from ntrade.brokers.dhan_transport import DhanTransport
        with pytest.raises(TypeError):
            DhanTransport(MagicMock(), rate_limiter=MagicMock())


class TestQuietLibraryPrints:
    """Tradehull prints 'Exception at calling ltp/Quote/OHLC as {...}' to stdout
    on a transient failure and swallows it into an empty dict. The transport
    must suppress that raw print (nTrade's own retry + envelope detection
    already handles the failure) while still returning the parsed value.
    """

    def test_get_ltp_suppresses_library_print_and_returns_ltp(self, capsys):
        tsl = MagicMock()

        def _noisy_ltp(names):
            print("Exception at calling ltp as {'status': 'failure'}")
            return {"TCS": 100.0}

        tsl.get_ltp_data.side_effect = _noisy_ltp
        transport, _, _ = _transport_with_gate(tsl)
        assert transport.get_ltp("TCS") == 100.0
        captured = capsys.readouterr()
        assert "Exception at calling ltp" not in captured.out
        assert "Exception at calling ltp" not in captured.err

    def test_get_ltp_retry_still_quiet_on_transient_failure(self, capsys):
        """The real-world case: first call fails (library prints + returns
        failure envelope), retry succeeds. Both must stay quiet and the LTP
        must come back."""
        tsl = MagicMock()

        def _flaky_ltp(names):
            print("Exception at calling ltp as {'status': 'failure', 'data': ''}")
            return {"status": "failure", "remarks": {}, "data": ""}

        calls = {"n": 0}

        def _ltp(names):
            calls["n"] += 1
            if calls["n"] == 1:
                return _flaky_ltp(names)
            return {"TCS": 100.0}

        tsl.get_ltp_data.side_effect = _ltp
        transport, _, _ = _transport_with_gate(tsl)
        from ntrade.execution.retry import RetryPolicy
        transport._retry_policy = RetryPolicy(max_retries=2, base_delay=0.0, jitter=0.0)
        assert transport.get_ltp("TCS") == 100.0
        assert calls["n"] == 2
        captured = capsys.readouterr()
        assert "Exception at calling ltp" not in captured.out
        assert "Exception at calling ltp" not in captured.err

    def test_get_quote_suppresses_library_print_and_enriches(self, capsys):
        tsl = MagicMock()

        def _noisy_ltp(names):
            return {"TCS": 100.0}

        def _noisy_quote(names):
            print("Exception at calling Quote as {'status': 'failure'}")
            return {"TCS": {"high": 105.0, "low": 99.0}}

        tsl.get_ltp_data.side_effect = _noisy_ltp
        tsl.get_quote_data.side_effect = _noisy_quote
        transport, _, _ = _transport_with_gate(tsl)
        quote = transport.get_quote("TCS")
        assert quote.ltp == 100.0
        assert quote.high == 105.0 and quote.low == 99.0
        captured = capsys.readouterr()
        assert "Exception at calling" not in captured.out
        assert "Exception at calling" not in captured.err

    def test_get_ohlc_suppresses_library_print_and_returns_dict(self, capsys):
        tsl = MagicMock()

        def _noisy_ohlc(names):
            print("Exception at calling OHLC as {'status': 'failure'}")
            return {"TCS": {"high": 105.0}}

        tsl.get_ohlc_data.side_effect = _noisy_ohlc
        transport, _, _ = _transport_with_gate(tsl)
        assert transport.get_ohlc("TCS") == {"high": 105.0}
        captured = capsys.readouterr()
        assert "Exception at calling" not in captured.out
        assert "Exception at calling" not in captured.err

    def test_quiet_scope_restores_stdout_after_call(self, capsys):
        """The redirect must not leak beyond the library call: a print made
        by the CALLER after get_ltp still reaches the terminal."""
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"TCS": 100.0}
        transport, _, _ = _transport_with_gate(tsl)
        transport.get_ltp("TCS")
        print("caller's own log line")
        captured = capsys.readouterr()
        assert "caller's own log line" in captured.out
