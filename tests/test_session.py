"""Tests for SessionState determinism (Phase 2)."""

from datetime import datetime

from ntrade.domain.session import MarketState, SessionState


def test_session_state_enter_with_now():
    ss = SessionState()
    ts = datetime(2026, 1, 1, 10, 0, 0)
    ss.enter(MarketState.OPEN, now=ts)
    assert ss.last_state_change == ts


def test_session_state_enter_without_now_uses_wall_clock():
    ss = SessionState()
    ss.enter(MarketState.OPEN)
    assert ss.last_state_change is not None
    # Should be close to now (within 2 seconds)
    assert (datetime.now() - ss.last_state_change).total_seconds() < 2
