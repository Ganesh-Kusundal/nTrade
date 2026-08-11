"""Deterministic synthetic order-book engine — market depth for simulation.

Complements ``synthesize_1m_ticks``: live NFO Full(21) streaming delivers
5-level depth (``DepthEvent``), but the synthetic feed and the backtest
simulator only published quote/tick events — so a strategy reading
``MarketDepth.bid_ask_imbalance()`` (e.g. the Valentini scalper's
``depth_imbalance_min`` confidence filter) always saw an *empty book* and
silently skipped the filter. This module closes that gap: an opt-in, seeded
order-book snapshot around each bar's LTP.

Invariants (all test-enforced):
  - deterministic: same seed + same inputs -> identical book
  - prices are tick-aligned; bids strictly below asks
  - ``MarketDepth.bid_ask_imbalance()`` over the book approximates the
    requested ``imbalance`` (positive = buy pressure, matching the filter's
    sign), within integer-quantity truncation toward zero
  - levels per side == ``levels``; qty/orders are plausible ints

Note on truncation: bid/ask = base ± ``int(base * imbalance)`` — the realized
book imbalance is at most ``imbalance`` in magnitude (never overshoots the
clamp), but sits slightly closer to zero than requested for non-integer
products. Tests are written against the tolerant bound, not exact equality.

Zero-parity: publishing depth is *opt-in* (``depth_levels>0``). With the
default ``depth_levels=0`` no ``DepthEvent`` is emitted, so backtest/replay
keep an empty book exactly as before — the strategy's depth filter stays
skipped and every existing test keeps its semantics.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Default book shape: two-tick spread around the tick-aligned mid, ~5 levels
# per side, seeded qty around 100 with per-level jitter.
DEFAULT_TICK_SIZE = 0.05
DEFAULT_LEVELS = 5
DEFAULT_SPREAD_TICKS = 1
DEFAULT_BASE_QTY = 100
DEFAULT_JITTER = 0.3


@dataclass(frozen=True)
class SimDepthLevel:
    price: float
    quantity: int
    orders: int


def synthesize_depth(
    ltp: float,
    *,
    tick_size: float = DEFAULT_TICK_SIZE,
    levels: int = DEFAULT_LEVELS,
    imbalance: float = 0.0,
    seed: int = 0,
    spread_ticks: int = DEFAULT_SPREAD_TICKS,
    base_qty: int = DEFAULT_BASE_QTY,
    jitter: float = DEFAULT_JITTER,
) -> tuple[tuple[SimDepthLevel, ...], tuple[SimDepthLevel, ...]]:
    """Build a deterministic N-level order book around ``ltp``.

    Returns ``(bids, asks)`` as tuples of :class:`SimDepthLevel`, priced on
    the ``tick_size`` grid with a ``spread_ticks`` gap on each side of the
    tick-aligned mid. Per-level quantities are jittered with the seeded RNG
    and then scaled so the *total* bid/ask qty reproduces ``imbalance``:
    bids carry ``(1 + imbalance)``, asks ``(1 - imbalance)`` — so the book's
    ``bid_ask_imbalance()`` equals ``imbalance`` (positive = buy pressure).

    ``imbalance`` is clamped to [-0.9, 0.9]; ``levels`` to [1, 20]. The
    realized book imbalance approximates (never exceeds) the request — see the
    module docstring for the truncation bound.
    """
    tick_size = float(tick_size)
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got {tick_size}")
    ltp = float(ltp)
    if ltp <= 0:
        raise ValueError(f"ltp must be > 0, got {ltp}")
    levels = max(1, min(20, int(levels)))
    imbalance = max(-0.9, min(0.9, float(imbalance)))
    rng = random.Random(seed)

    # Tick-aligned reference mid (round half away from zero on the grid).
    mid = round(ltp / tick_size) * tick_size
    spread_ticks = max(0, int(spread_ticks))

    bids: list[SimDepthLevel] = []
    asks: list[SimDepthLevel] = []
    for k in range(levels):
        j = 1.0 + rng.uniform(-jitter, jitter)
        base = max(1, int(base_qty * j))
        # Symmetric construction: bid/ask = base ± delta, so the book's
        # imbalance is delta/base ≈ imbalance, never distorted by int()
        # truncation of the two sides independently. Truncation toward zero
        # (not round) guarantees the *aggregate* imbalance never exceeds the
        # clamp: sum(floor(b_i·i)) ≤ i·sum(b_i).
        delta = int(base * imbalance)
        bid_qty = max(1, base + delta)
        ask_qty = max(1, base - delta)
        orders = max(1, rng.randrange(1, 6))
        bid_price = round(mid - (spread_ticks + k + 1) * tick_size, 4)
        ask_price = round(mid + (spread_ticks + k + 1) * tick_size, 4)
        bids.append(SimDepthLevel(price=bid_price, quantity=bid_qty, orders=orders))
        asks.append(SimDepthLevel(price=ask_price, quantity=ask_qty, orders=orders))
    return tuple(bids), tuple(asks)


def depth_to_wire(
    bids: tuple[SimDepthLevel, ...], asks: tuple[SimDepthLevel, ...],
) -> tuple[tuple, tuple]:
    """Convert SimDepthLevel books to the DepthEvent wire format (price, qty, orders)."""
    return (tuple((b.price, b.quantity, b.orders) for b in bids),
            tuple((a.price, a.quantity, a.orders) for a in asks))


def bar_imbalance(open_: float, close: float, *, scale: float = 1.0) -> float:
    """Per-bar buy-pressure bias derived from the bar's own move.

    Up bars (close > open) get positive (buy) pressure, down bars negative
    (sell) pressure, flat bars neutral — so a depth-aware strategy's filter
    discriminates *between setups* instead of seeing one constant bias across
    the whole run (the ``"constant"`` mode's limitation). ``scale`` bounds the
    magnitude (e.g. 0.5 keeps the book inside ±0.5 while preserving the sign).
    """
    if close > open_:
        return scale
    if close < open_:
        return -scale
    return 0.0
