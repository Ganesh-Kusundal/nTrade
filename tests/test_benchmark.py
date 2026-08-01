"""Latency benchmark helper (G2-E3)."""
import pytest

from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.bench import measure_tick_throughput


def test_throughput_measurement_returns_dict():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    stats = measure_tick_throughput(k, n_ticks=100)
    assert stats["ticks"] == 100
    assert stats["events_per_sec"] > 0
    assert stats["wall_seconds"] >= 0
