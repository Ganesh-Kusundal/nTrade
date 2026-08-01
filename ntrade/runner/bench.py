"""Micro-benchmarks for the kernel event pipeline (feeds .benchmarks/)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from ntrade.events.market import TickEvent


def measure_tick_throughput(kernel, *, n_ticks: int = 1000, symbol: str = "BENCH") -> dict:
    from ntrade.domain.instruments.cash import Equity
    kernel.register(Equity(symbol))
    start = time.perf_counter()
    ts = datetime(2026, 7, 30, 9, 15)
    for i in range(n_ticks):
        kernel.bus.publish(TickEvent(symbol=symbol, exchange="NSE",
                                     price=100.0 + (i % 10), quantity=1,
                                     ts=ts + timedelta(milliseconds=i)))
    wall = time.perf_counter() - start
    return {
        "ticks": n_ticks,
        "wall_seconds": round(wall, 6),
        "events_per_sec": round(n_ticks / wall, 1) if wall else 0.0,
    }
