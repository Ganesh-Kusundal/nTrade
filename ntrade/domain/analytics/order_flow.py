"""Order-flow approximations — CVD + absorption, given Dhan's data ceiling.

Dhan provides NO trade tape (no per-trade aggressor side, no historical
ticks). Everything here is an explicitly-labelled approximation:

- ``CvdTracker`` — tick-rule cumulative volume delta fed from the live
  websocket (price up vs last trade ⇒ buyer-initiated, down ⇒ seller).
- ``cvd_from_ohlcv`` — backtest proxy: bar side from close-vs-open, volume
  as size.
- ``detect_absorptions`` — "big volume, no price" bars (Valentini's phase-1
  signal), fully computable from OHLCV + volume.

Never present these as true institutional order flow.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


class CvdTracker:
    """Tick-rule cumulative volume delta.

    Live ticks arrive as (price, last-traded-quantity). A tick at a price
    above the previous tick is classified buyer-initiated (delta += qty);
    below ⇒ seller-initiated (delta -= qty); an unchanged price carries the
    previous classification (standard tick rule).
    """

    def __init__(self):
        self._last_price: float | None = None
        self._side: float = 0.0  # -1 sell / 0 flat / +1 buy
        self.delta: float = 0.0  # signed volume since tracker start

    def update(self, price: float, qty: float) -> float:
        """Classify one tick, accumulate it, return the signed contribution.

        A zero-qty tick (e.g. a quote-only update) refreshes the reference
        price but never re-classifies the carried side — only actual trades
        (qty > 0) move the side, so a quote interleaved with trades can't
        contaminate the next trade's classification.
        """
        qty = float(qty or 0.0)
        price = float(price)
        if self._last_price is not None and qty > 0:
            if price > self._last_price:
                self._side = 1.0
            elif price < self._last_price:
                self._side = -1.0
            # unchanged price: keep the carried side
        self._last_price = price
        contribution = self._side * qty
        self.delta += contribution
        return contribution

    @property
    def cvd(self) -> float:
        """Cumulative (buy - sell) volume."""
        return self.delta

    @property
    def last_price(self) -> float | None:
        return self._last_price


def cvd_from_ohlcv(df: pd.DataFrame) -> pd.Series:
    """Backtest CVD proxy: cumulative ``sign(close - open) * volume``.

    A bar is buyer-initiated when it closes above its open. This is a rough
    stand-in for true CVD — trade-tape data does not exist on Dhan.
    """
    if df is None or df.empty or "close" not in df or "open" not in df:
        return pd.Series(dtype=float)
    side = (df["close"].astype(float) - df["open"].astype(float)).apply(
        lambda d: 1.0 if d > 0 else (-1.0 if d < 0 else 0.0))
    vol = df.get("volume", pd.Series(0.0, index=df.index)).astype(float)
    return (side * vol).cumsum()


@dataclass(frozen=True)
class Absorption:
    """A 'big volume, no price movement' bar (Valentini phase-1)."""

    bar_index: int
    price: float
    volume: float
    side: str   # "BUY" | "SELL"
    strength: float  # 0..1 normalized volume excess


def detect_absorptions(
    bars: pd.DataFrame,
    avg_volume_mult: float = 1.5,
    range_threshold: float = 0.5,
    range_size: float | None = None,
    window: int = 20,
) -> list[Absorption]:
    """Flag absorption bars: volume >> rolling average AND compressed range.

    Conditions (per bar):
      1. ``volume >= avg_volume_mult * avg_volume`` — rolling mean over the
         previous ``window`` bars (excludes the bar itself).
      2. ``(high - low) <= range_threshold * range_size`` when ``range_size``
         is given; otherwise ``(high - low)`` is compared against the rolling
         mean range (self-scaling).

    Side: close >= open ⇒ BUY else SELL. Strength: volume excess normalized
    to 0..1 (``(vol/avg - mult) / (mult - 1)`` clipped).
    """
    if bars is None or bars.empty or "volume" not in bars or "high" not in bars:
        return []
    vols = bars["volume"].astype(float)
    avg = vols.shift(1).rolling(window, min_periods=1).mean()
    avg = avg.fillna(vols.expanding().mean())
    avg = avg.replace(0, pd.NA).fillna(vols.mean())

    ranges = (bars["high"].astype(float) - bars["low"].astype(float))
    avg_range = ranges.shift(1).rolling(window, min_periods=1).mean().fillna(
        ranges.expanding().mean()).replace(0, pd.NA).fillna(ranges.mean())

    out: list[Absorption] = []
    for i in range(len(bars)):
        vol = float(vols.iloc[i])
        a = float(avg.iloc[i])
        if a <= 0 or vol < avg_volume_mult * a:
            continue
        r = float(ranges.iloc[i])
        limit = (range_threshold * float(range_size)
                 if range_size and range_size > 0
                 else range_threshold * float(avg_range.iloc[i]))
        if r > limit:
            continue
        close = float(bars["close"].iloc[i]) if "close" in bars else 0.0
        open_ = float(bars["open"].iloc[i]) if "open" in bars else close
        side = "BUY" if close >= open_ else "SELL"
        excess = (vol / a - 1.0) / (avg_volume_mult - 1.0) if avg_volume_mult > 1 else 1.0
        strength = max(0.0, min(1.0, excess))
        out.append(Absorption(
            bar_index=i, price=close or float(ranges.iloc[i]), volume=vol,
            side=side, strength=strength,
        ))
    return out
