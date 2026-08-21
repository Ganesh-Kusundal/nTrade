"""The hand-maintained interval maps must not disagree.

The pump's _SPAN_MIN had "1D": 375 vs kernel's 86400 — same string,
two spans. Now both derive from ntrade.domain.timeframes, so this
test pins that derivation.
"""
from ntrade.engines.candle_engine import _INTERVAL_SECONDS
from api.live import _SPAN_MIN
from ntrade.domain.timeframes import interval_span_seconds


def test_pump_and_kernel_agree_on_span():
    for interval, span_min in _SPAN_MIN.items():
        seconds = span_min * 60
        key = interval if interval in _INTERVAL_SECONDS else interval.lower()
        assert _INTERVAL_SECONDS.get(key) == seconds, (
            f"{interval}: pump={seconds}s kernel={_INTERVAL_SECONDS.get(key)}s")


def test_span_matches_canonical_timeframes():
    for interval, span_min in _SPAN_MIN.items():
        assert interval_span_seconds(interval) == span_min * 60
