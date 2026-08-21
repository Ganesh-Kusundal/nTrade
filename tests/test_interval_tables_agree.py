"""The hand-maintained interval maps must not disagree.

The pump's _SPAN_MIN had "1D": 375 (a session bar) while the kernel's
candle engine says 86400 (a calendar day) — the same interval string meant
two different spans in the same process. This test pins one truth.
"""
from ntrade.engines.candle_engine import _INTERVAL_SECONDS
from api.live import _SPAN_MIN


def test_pump_and_kernel_agree_on_span():
    for interval, span_min in _SPAN_MIN.items():
        seconds = span_min * 60
        key = interval if interval in _INTERVAL_SECONDS else interval.lower()
        assert _INTERVAL_SECONDS.get(key) == seconds, (
            f"{interval}: pump={seconds}s kernel={_INTERVAL_SECONDS.get(key)}s")
