"""Deterministic synthetic tick engine: extrapolate 1-minute OHLCV into
1-second ticks that respect the bar's high/low.

Invariants (all test-enforced):
  - one tick per second (seconds=60 by default)
  - tick[0] == open, tick[-1] == close
  - every price within [low, high]
  - max(price) == high and min(price) == low (both extremes touched)
  - sum(tick.quantity) == bar volume
  - same seed + same bar -> identical ticks
  - |idx(high) - idx(low)| >= max(1, min(MIN_ANCHOR_GAP, (seconds-3)//2))
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_ANCHOR_GAP = 5  # minimum seconds between hi and lo anchors


@dataclass(frozen=True)
class SimTick:
    ts: datetime
    price: float
    quantity: int


def _interp(anchors: list[tuple[int, float]], n: int) -> list[float]:
    """Piecewise-linear path through (index, price) anchors, length n."""
    prices = [0.0] * n
    for (i0, p0), (i1, p1) in zip(anchors, anchors[1:]):
        for i in range(i0, i1 + 1):
            frac = (i - i0) / (i1 - i0) if i1 != i0 else 0.0
            prices[i] = p0 + (p1 - p0) * frac
    return prices


def _distribute_volume(volume: int, prices: list[float]) -> list[int]:
    """Split bar volume across ticks proportional to per-second |move|."""
    volume = int(volume or 0)
    if volume <= 0:
        return [0] * len(prices)
    moves = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    total = sum(moves) or 1.0
    qty = [0] * len(prices)
    for i, m in enumerate(moves, start=1):
        qty[i] = int(volume * m / total)
    qty[-1] += volume - sum(qty)  # remainder lands on the last tick
    return qty


def synthesize_1m_ticks(bar_ts: datetime, open_: float, high: float, low: float,
                        close: float, volume: int, *, seed: int = 0,
                        seconds: int = 60) -> list[SimTick]:
    if high < low:
        raise ValueError(f"high {high} < low {low}")
    if seconds < 4:
        raise ValueError("seconds must be >= 4")
    rng = random.Random(seed)
    n = seconds
    # max(1, ...) preserves lo != hi for n=4 where (n-3)//2 == 0
    min_gap = max(1, min(MIN_ANCHOR_GAP, (n - 3) // 2))
    hi = rng.randrange(1, n - 1)
    lo = rng.randrange(1, n - 1)
    while abs(hi - lo) < min_gap:
        lo = rng.randrange(1, n - 1)
    anchors = sorted([(0, open_), (hi, high), (lo, low), (n - 1, close)])
    prices = _interp(anchors, n)
    noise = (high - low) * 0.02
    for i in range(n):
        if i in (0, hi, lo, n - 1):
            continue
        prices[i] = min(high, max(low, prices[i] + rng.uniform(-noise, noise)))
    qty = _distribute_volume(volume, prices)
    # round BEFORE re-pinning the anchors: round() would otherwise break the
    # "high/low touched" invariant for non-integer prices (e.g. live NIFTY).
    for i in range(n):
        prices[i] = round(prices[i], 2)
    prices[0] = open_
    prices[hi] = high
    prices[lo] = low
    prices[n - 1] = close
    return [SimTick(ts=bar_ts + timedelta(seconds=i), price=prices[i],
                    quantity=qty[i]) for i in range(n)]
