"""Integration tests for the screener subsystem.

Verifies:
- ScreenerFacade composes + normalizes + ranks real scanners
- WatchlistReady event is published when screener runs
- Strategy.on_watchlist hook receives results (zero-parity: same path live + backtest)
- TradingSession.screener() is lazy + cached
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from ntrade.domain.market.quote import Quote
from ntrade.domain.scanner import ScannerResult
from ntrade.domain.screener import ScreenerFacade
from ntrade.events.market import WatchlistReady
from ntrade.scanners.builtin import GapScanner, VolumeSpikeScanner


# ============================================================ helpers

def _make_session_with_instruments(*instruments):
    from ntrade.kernel.trading_session import TradingSession
    session = TradingSession.paper()
    for inst in instruments:
        session._kernel.ctx.instruments[inst.symbol] = inst
    return session


def _make_instrument(symbol: str, *, ltp: float = 0, prev_close: float = 0,
                     volume: int = 0, high: float = 0, low: float = 0,
                     indicators: dict | None = None):
    inst = MagicMock()
    inst.symbol = symbol
    inst._quote = Quote(ltp=ltp, prev_close=prev_close, volume=volume,
                        high=high, low=low)
    inst._indicators = indicators or {}

    market = MagicMock()
    market.ltp.return_value = ltp
    market.prev_close.return_value = prev_close
    market.volume.return_value = volume
    market.quote.return_value = inst._quote
    inst.market = market
    return inst


# ============================================================ ScreenerFacade integration

class TestScreenerFacadeIntegration:
    def test_screener_composes_and_normalizes(self):
        """Gap (10%) + Volume (200k) on the same instrument → single merged result."""
        inst = _make_instrument("AAA", ltp=220, prev_close=200, volume=200_000)
        session = _make_session_with_instruments(inst)

        sc = ScreenerFacade(session)
        sc.register((GapScanner(), 0.4), (VolumeSpikeScanner(), 0.6))
        results = sc.run(min_volume=100_000, min_gap_pct=1.0)

        assert len(results) == 1
        assert results[0].instrument.symbol == "AAA"
        assert results[0].rank == 1
        assert 0.0 < results[0].score <= 1.0  # normalized + weighted

    def test_weights_affect_ranking(self):
        """Higher weight on gap → gap instrument outranks vol-only instrument."""
        gap_only = _make_instrument("GAP", ltp=220, prev_close=200, volume=50_000)
        vol_only = _make_instrument("VOL", ltp=101, prev_close=100, volume=200_000)
        session = _make_session_with_instruments(gap_only, vol_only)

        # Gap weighted 0.9, vol 0.1 → gap-only should win
        sc = ScreenerFacade(session)
        sc.register((GapScanner(), 0.9), (VolumeSpikeScanner(), 0.1))
        results = sc.run(min_volume=100_000, min_gap_pct=1.0)
        assert len(results) == 2
        assert results[0].instrument.symbol == "GAP"

    def test_watchlist_published(self):
        """Running the screener publishes a WatchlistReady event on the bus."""
        inst = _make_instrument("AAA", ltp=220, prev_close=200, volume=200_000)
        session = _make_session_with_instruments(inst)
        session.start()

        sc = ScreenerFacade(session)
        sc.register((GapScanner(), 1.0))
        sc.run(min_gap_pct=1.0)

        events = [e for e in session._kernel.bus.history if isinstance(e, WatchlistReady)]
        assert len(events) == 1
        assert len(events[0].results) == 1
        assert events[0].results[0].instrument.symbol == "AAA"

    def test_watchlist_not_published_for_empty_results(self):
        """No results → no event (avoid noise on every tick)."""
        session = _make_session_with_instruments()
        session.start()

        sc = ScreenerFacade(session)
        sc.register((GapScanner(), 1.0))
        sc.run(min_gap_pct=50.0)  # nothing gaps 50%

        events = [e for e in session._kernel.bus.history if isinstance(e, WatchlistReady)]
        assert len(events) == 0


# ============================================================ Strategy hook integration

class TestStrategyOnWatchlist:
    def test_strategy_receives_watchlist_event(self):
        """A strategy with on_watchlist gets called when screener publishes."""
        from ntrade.engines.strategy_engine import Strategy

        seen = []

        class WatchlistStrategy(Strategy):
            name = "wl_strategy"

            def on_watchlist(self, event):
                seen.append(event)

        inst_a = _make_instrument("AAA", ltp=220, prev_close=200, volume=200_000)
        inst_b = _make_instrument("BBB", ltp=102, prev_close=100, volume=50_000)
        session = _make_session_with_instruments(inst_a, inst_b)
        session.start()

        session._runner.add(WatchlistStrategy(), name="wl_strategy")

        sc = ScreenerFacade(session)
        sc.register((GapScanner(), 1.0))
        sc.run(min_gap_pct=1.0)

        assert len(seen) == 1
        assert len(seen[0].results) == 2  # both gap up >= 1%
        assert seen[0].results[0].rank == 1

    def test_strategy_hook_is_optional(self):
        """A strategy without on_watchlist must not error when the event fires."""
        from ntrade.engines.strategy_engine import Strategy

        class DummyStrategy(Strategy):
            name = "dummy"

        inst = _make_instrument("AAA", ltp=220, prev_close=200, volume=200_000)
        session = _make_session_with_instruments(inst)
        session.start()
        session._runner.add(DummyStrategy(), name="dummy")

        sc = ScreenerFacade(session)
        sc.register((GapScanner(), 1.0))
        sc.run(min_gap_pct=1.0)  # should not raise


# ============================================================ TradingSession integration

class TestSessionScreener:
    def test_screener_lazy_and_cached(self):
        session = _make_session_with_instruments()
        assert session.screener() is session.screener()

    def test_ctx_session_backref(self):
        """strategy.ctx.session gives access to the full session incl. screener."""
        from ntrade.engines.strategy_engine import Strategy

        class RefStrategy(Strategy):
            name = "ref"

            def on_watchlist(self, event):
                # Verify the strategy can reach the session via ctx
                assert self.ctx.session is not None
                assert hasattr(self.ctx.session, "screener")

        session = _make_session_with_instruments(
            _make_instrument("AAA", ltp=220, prev_close=200, volume=200_000)
        )
        session.start()
        session._runner.add(RefStrategy(), name="ref")

        sc = ScreenerFacade(session)
        sc.register((GapScanner(), 1.0))
        sc.run(min_gap_pct=1.0)

    def test_ctx_session_cleared_on_stop(self):
        """session.ctx.session is set to None after stop()."""
        session = _make_session_with_instruments()
        session.start()
        session.stop()
        assert session._kernel.ctx.session is None
