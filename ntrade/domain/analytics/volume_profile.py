"""Volume profile — horizontal volume distribution (POC / VAH / VAL).

The Valentini model's "location" step: which price levels carry the most
volume. ``build_volume_profile`` buckets a bar frame by price and reports the
Point of Control (POC = busiest bucket) plus the 68% Value Area (VAH/VAL),
expanded outward from the POC exactly as the build guide describes.

Allocation: each bar's volume is attributed to the bucket containing the
bar's *midpoint* ``(high + low) / 2`` — a documented simplification (no
trade tape on Dhan means per-level buy/sell volume is unavailable; ``delta``
stays 0 unless a ``buy_volume`` series is supplied).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

VALUE_AREA_PCT = 0.68


@dataclass(frozen=True)
class VPLevel:
    """Volume at a single price bucket."""

    price: float        # bucket midpoint
    volume: float       # total traded volume allocated to this bucket
    delta: float = 0.0  # buy - sell (0 without a buy/sell split)
    total: float = 0.0  # buy + sell (== volume when no split supplied)


@dataclass(frozen=True)
class VolumeProfile:
    levels: tuple[VPLevel, ...]
    poc: float   # Point of Control (busiest bucket midpoint)
    vah: float   # Value Area High
    val: float   # Value Area Low
    step: float  # bucket width


def _default_step(bars: pd.DataFrame) -> float:
    """A sane bucket width: 1/50th of the session's price span, min 0.05."""
    span = float(bars["high"].max() - bars["low"].min())
    if span <= 0:
        return 1.0
    return max(round(span / 50.0, 2), 0.05)


def build_volume_profile(
    bars: pd.DataFrame,
    step: float | None = None,
    buy_volume: Iterable[float] | None = None,
) -> VolumeProfile:
    """Build a volume profile over a bar frame.

    Args:
        bars: frame with ``high``/``low``/``volume`` (and ``open``/``close``
            for delta derivation when no ``buy_volume`` is given).
        step: bucket width in price units (default: 1/50th of the span).
        buy_volume: optional per-bar buyer-initiated volume; when provided
            each level's ``delta = buy - (volume - buy)``. Without it delta
            is derived from close-vs-open, falling back to 0.

    Empty / degenerate input yields an empty profile with ``poc=val=vah=0``.
    """
    if bars is None or bars.empty or "high" not in bars or "volume" not in bars:
        return VolumeProfile(levels=(), poc=0.0, vah=0.0, val=0.0, step=step or 0.0)

    width = step if step and step > 0 else _default_step(bars)
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())

    # Bucket k covers [k*width - width/2, k*width + width/2), centred on the
    # multiple of `width` (half-up rounding). Centred buckets keep profiles
    # comparable across sessions AND make integer prices land on integer
    # bucket centres — the level's ``price`` is its centre.
    def _bucket_index(price: float) -> int:
        return int(math.floor(price / width + 0.5))

    k_start = _bucket_index(lo)
    k_end = max(k_start, _bucket_index(hi))
    n_buckets = k_end - k_start + 1

    vols = [0.0] * n_buckets
    buys = [0.0] * n_buckets
    buy_list = list(buy_volume) if buy_volume is not None else None
    if buy_list is not None and len(buy_list) != len(bars):
        raise ValueError(
            f"buy_volume length {len(buy_list)} != bars length {len(bars)}")

    has_split = buy_list is not None
    for i, (_, row) in enumerate(bars.iterrows()):
        mid = (float(row["high"]) + float(row["low"])) / 2.0
        v = float(row.get("volume", 0) or 0)
        b = _bucket_index(mid) - k_start
        b = max(0, min(b, n_buckets - 1))
        vols[b] += v
        if has_split:
            buys[b] += float(buy_list[i] or 0.0)

    total = sum(vols)
    if total <= 0:
        return VolumeProfile(levels=(), poc=0.0, vah=0.0, val=0.0, step=width)

    # POC: bucket with the maximum volume.
    poc_idx = max(range(n_buckets), key=lambda b: vols[b])

    # Value area: expand from POC to the higher-volume neighbour until 68%.
    included = {poc_idx}
    acc = vols[poc_idx]
    left, right = poc_idx - 1, poc_idx + 1
    while acc < VALUE_AREA_PCT * total and (left >= 0 or right < n_buckets):
        if left < 0:
            pick = right
        elif right >= n_buckets:
            pick = left
        else:
            pick = left if vols[left] >= vols[right] else right
        included.add(pick)
        acc += vols[pick]
        if pick == left:
            left -= 1
        else:
            right += 1

    levels = tuple(
        VPLevel(
            price=round((k_start + b) * width, 4),
            volume=round(vols[b], 4),
            delta=round(buys[b] - (vols[b] - buys[b]), 4) if has_split else 0.0,
            total=round(vols[b], 4),
        )
        for b in range(n_buckets)
    )
    return VolumeProfile(
        levels=levels,
        poc=round((k_start + poc_idx) * width, 4),
        vah=round((k_start + max(included)) * width, 4),
        val=round((k_start + min(included)) * width, 4),
        step=width,
    )
