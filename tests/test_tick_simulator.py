"""Deterministic 1-second tick synthesis from 1m OHLCV bars (G2-A1)."""
from datetime import datetime

import pytest

from ntrade.sim.tick_simulator import synthesize_1m_ticks


def _bar():
    return datetime(2026, 7, 30, 9, 15), 100.0, 105.0, 98.0, 102.0, 600


def test_returns_one_tick_per_second():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    assert len(ticks) == 60
    for i, t in enumerate(ticks):
        assert (t.ts - ts).total_seconds() == i


def test_anchors_open_and_close():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    assert ticks[0].price == o
    assert ticks[-1].price == c


def test_prices_stay_within_high_low():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    for t in ticks:
        assert l <= t.price <= h


def test_high_and_low_touched():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    prices = [t.price for t in ticks]
    assert max(prices) == h
    assert min(prices) == l


def test_volume_sums_to_bar_volume():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    assert sum(t.quantity for t in ticks) == v


def test_deterministic_same_seed():
    ts, o, h, l, c, v = _bar()
    a = synthesize_1m_ticks(ts, o, h, l, c, v, seed=7)
    b = synthesize_1m_ticks(ts, o, h, l, c, v, seed=7)
    assert [(t.price, t.quantity) for t in a] == [(t.price, t.quantity) for t in b]


def test_different_seed_different_path():
    ts, o, h, l, c, v = _bar()
    a = synthesize_1m_ticks(ts, o, h, l, c, v, seed=1)
    b = synthesize_1m_ticks(ts, o, h, l, c, v, seed=2)
    assert [(t.price, t.quantity) for t in a] != [(t.price, t.quantity) for t in b]


def test_flat_bar_is_flat():
    ts = datetime(2026, 7, 30, 9, 15)
    ticks = synthesize_1m_ticks(ts, 100.0, 100.0, 100.0, 100.0, 100)
    assert all(t.price == 100.0 for t in ticks)


def test_invalid_range_raises():
    ts = datetime(2026, 7, 30, 9, 15)
    with pytest.raises(ValueError):
        synthesize_1m_ticks(ts, 100.0, 90.0, 95.0, 100.0, 100)


def test_no_full_range_jump_in_one_second():
    """Non-adjacent anchors prevent full bar-range jumps for n >= 6."""
    ts, o, h, l, c, v = _bar()
    bar_range = h - l
    for seed in range(200):
        ticks = synthesize_1m_ticks(ts, o, h, l, c, v, seed=seed)
        prices = [t.price for t in ticks]
        max_jump = max(abs(prices[i] - prices[i - 1]) for i in range(1, len(prices)))
        assert max_jump < bar_range, (
            f"seed={seed}: 1s jump {max_jump:.2f} >= bar range {bar_range:.2f}"
        )


def test_heg_no_adjacent_spike():
    """Regression: HEG seed=99 previously had a 36-point spike in 1 second."""
    ts = datetime(2026, 7, 30, 9, 15)
    ticks = synthesize_1m_ticks(ts, 1620.50, 1648.75, 1612.30, 1635.00, 10000, seed=99)
    prices = [t.price for t in ticks]
    bar_range = 1648.75 - 1612.30
    max_jump = max(abs(prices[i] - prices[i - 1]) for i in range(1, len(prices)))
    assert max_jump < bar_range / 3


@pytest.mark.parametrize("seconds", [4, 5, 6, 7, 10])
def test_small_seconds_invariants(seconds):
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v, seconds=seconds)
    assert len(ticks) == seconds
    assert ticks[0].price == o
    assert ticks[-1].price == c
    prices = [t.price for t in ticks]
    assert max(prices) == h
    assert min(prices) == l
    assert sum(t.quantity for t in ticks) == v
