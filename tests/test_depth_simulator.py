"""Synthetic depth engine — deterministic order-book generation (G2-A3).

Invariants: seeded determinism; tick-aligned prices with bids < asks;
bid_ask_imbalance() of the book matches the requested imbalance; level counts
and plausibility of qty/orders.
"""
import pytest

from ntrade.domain.market.depth import DepthLevel, MarketDepth
from ntrade.sim.depth_simulator import (
    SimDepthLevel, bar_imbalance, depth_to_wire, synthesize_depth,
)


def _book_imbalance(bids, asks) -> float:
    book = MarketDepth(
        symbol="SYM",
        bids=tuple(DepthLevel(*level) for level in depth_to_wire(bids, asks)[0]),
        asks=tuple(DepthLevel(*level) for level in depth_to_wire(bids, asks)[1]),
    )
    return book.bid_ask_imbalance()


def test_returns_requested_levels_per_side():
    bids, asks = synthesize_depth(24000.0, levels=5)
    assert len(bids) == 5
    assert len(asks) == 5


def test_deterministic_same_seed():
    a = synthesize_depth(24000.0, seed=7)
    b = synthesize_depth(24000.0, seed=7)
    assert a == b


def test_prices_tick_aligned_bids_below_asks():
    bids, asks = synthesize_depth(24000.0, tick_size=0.05, levels=5)
    for b, a in zip(bids, asks):
        assert b.price < a.price
        # tick-aligned: price / tick is (near-)integer
        assert abs(round(b.price / 0.05) - b.price / 0.05) < 1e-6
        assert abs(round(a.price / 0.05) - a.price / 0.05) < 1e-6


def test_best_bid_ask_straddle_ltp():
    bids, asks = synthesize_depth(24000.0, tick_size=0.05, spread_ticks=1)
    assert bids[0].price < 24000.0 < asks[0].price


def test_imbalance_positive_is_buy_pressure():
    bids, asks = synthesize_depth(24000.0, imbalance=0.5, seed=3)
    assert _book_imbalance(bids, asks) > 0.4


def test_imbalance_negative_is_sell_pressure():
    bids, asks = synthesize_depth(24000.0, imbalance=-0.5, seed=3)
    assert _book_imbalance(bids, asks) < -0.4


def test_zero_imbalance_is_neutral():
    bids, asks = synthesize_depth(24000.0, imbalance=0.0, seed=3)
    assert abs(_book_imbalance(bids, asks)) < 0.05


def test_imbalance_clamped_to_plus_0_9():
    bids, asks = synthesize_depth(24000.0, imbalance=5.0, seed=1)
    assert _book_imbalance(bids, asks) <= 0.9


def test_levels_clamped_low():
    bids, asks = synthesize_depth(24000.0, levels=0)
    assert len(bids) == 1 and len(asks) == 1


def test_qty_and_orders_plausible():
    bids, asks = synthesize_depth(24000.0, levels=5, seed=2)
    for b, a in zip(bids, asks):
        assert b.quantity >= 1 and a.quantity >= 1
        assert 1 <= b.orders <= 5 and 1 <= a.orders <= 5


def test_different_seed_different_book():
    a = synthesize_depth(24000.0, seed=1)
    b = synthesize_depth(24000.0, seed=2)
    assert a != b


def test_invalid_tick_size_raises():
    with pytest.raises(ValueError):
        synthesize_depth(24000.0, tick_size=0.0)


def test_non_positive_ltp_raises():
    with pytest.raises(ValueError):
        synthesize_depth(0.0)


def test_bar_imbalance_sign():
    assert bar_imbalance(100.0, 102.0) > 0        # up bar -> buy pressure
    assert bar_imbalance(102.0, 100.0) < 0        # down bar -> sell pressure
    assert bar_imbalance(100.0, 100.0) == 0.0     # flat -> neutral


def test_bar_imbalance_scale():
    assert bar_imbalance(100.0, 102.0, scale=0.5) == 0.5
    assert bar_imbalance(102.0, 100.0, scale=0.3) == -0.3


def test_bar_mode_book_imbalance_matches_bar_move():
    """Per-bar mode: an up bar's book is buy-biased, a down bar's sell-biased."""
    up = synthesize_depth(100.0, imbalance=bar_imbalance(99.0, 101.0, scale=0.5), seed=4)
    down = synthesize_depth(100.0, imbalance=bar_imbalance(101.0, 99.0, scale=0.5), seed=4)
    assert _book_imbalance(*up) > 0
    assert _book_imbalance(*down) < 0


def test_returns_simdepthlevel_instances():
    bids, _ = synthesize_depth(24000.0)
    assert all(isinstance(b, SimDepthLevel) for b in bids)
