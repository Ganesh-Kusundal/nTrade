"""Tests for the scanner subsystem (Phase F).

Covers:
- ScannerResult frozen dataclass
- Scanner ABC (top() ranking)
- ScannerFacade (named entry points, custom scanner, register)
- All 5 built-in scanners: Gap, VolumeSpike, Momentum, Breakout, Imbalance
- TradingSession.scanner() integration
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from ntrade.domain.market.depth import DepthLevel, MarketDepth
from ntrade.domain.market.quote import Quote
from ntrade.domain.scanner import Scanner, ScannerFacade, ScannerResult
from ntrade.scanners.builtin import (
    BreakoutScanner,
    GapScanner,
    ImbalanceScanner,
    MomentumScanner,
    VolumeSpikeScanner,
)


# ============================================================ helpers

def _make_session_with_instruments(*instruments):
    """Create a paper session with instruments pre-registered in the kernel."""
    from ntrade.kernel.trading_session import TradingSession
    session = TradingSession.paper()
    for inst in instruments:
        session._kernel.ctx.instruments[inst.symbol] = inst
    return session


def _make_instrument(symbol: str, *, ltp: float = 0, prev_close: float = 0,
                     volume: int = 0, high: float = 0, low: float = 0,
                     indicators: dict | None = None,
                     depth: MarketDepth | None = None):
    """Build a lightweight mock instrument with the given quote/depth/indicators."""
    inst = MagicMock()
    inst.symbol = symbol
    inst._quote = Quote(ltp=ltp, prev_close=prev_close, volume=volume,
                        high=high, low=low)
    inst._indicators = indicators or {}
    if depth is not None:
        inst._depth = depth
    else:
        inst._depth = MarketDepth.empty(symbol)
    return inst


# ============================================================ ScannerResult

class TestScannerResult:
    def test_frozen_dataclass(self):
        inst = MagicMock()
        r = ScannerResult(
            instrument=inst, scanner_name="test", score=1.5,
            signal="BUY", matched_conditions=("cond_a",),
            indicator_values={"rsi": 65.0}, rank=1,
            metadata={"note": "ok"}, timestamp=datetime.now(),
        )
        assert r.signal == "BUY"
        assert r.score == 1.5
        assert r.rank == 1
        with pytest.raises(AttributeError):
            r.signal = "SELL"  # frozen

    def test_defaults(self):
        inst = MagicMock()
        r = ScannerResult(instrument=inst, scanner_name="x", score=0, signal="NEUTRAL")
        assert r.matched_conditions == ()
        assert r.indicator_values == {}
        assert r.rank == 0
        assert r.metadata == {}
        assert r.timestamp is None


# ============================================================ Scanner ABC

class TestScannerABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Scanner()

    def test_concrete_subclass(self):
        class MyScanner(Scanner):
            name = "my_scanner"
            def scan(self, session, **kw):
                return [
                    ScannerResult(instrument=MagicMock(), scanner_name=self.name,
                                  score=2.0, signal="BUY"),
                    ScannerResult(instrument=MagicMock(), scanner_name=self.name,
                                  score=5.0, signal="SELL"),
                ]

        s = MyScanner()
        session = _make_session_with_instruments()
        results = s.scan(session)
        assert len(results) == 2

    def test_top_ranks_by_score(self):
        class MyScanner(Scanner):
            name = "ranker"
            def scan(self, session, **kw):
                return [
                    ScannerResult(instrument=MagicMock(), scanner_name=self.name,
                                  score=s, signal="BUY")
                    for s in (1.0, 5.0, 3.0, 9.0, 2.0)
                ]

        s = MyScanner()
        session = _make_session_with_instruments()
        top3 = s.top(session, 3)
        assert len(top3) == 3
        assert top3[0].score == 9.0 and top3[0].rank == 1
        assert top3[1].score == 5.0 and top3[1].rank == 2
        assert top3[2].score == 3.0 and top3[2].rank == 3


# ============================================================ ScannerFacade

class TestScannerFacade:
    def test_builtins_registered(self):
        session = _make_session_with_instruments()
        facade = ScannerFacade(session)
        assert "gap" in facade._scanners
        assert "volume_spike" in facade._scanners
        assert "momentum" in facade._scanners
        assert "breakout" in facade._scanners
        assert "imbalance" in facade._scanners

    def test_custom_scanner(self):
        session = _make_session_with_instruments()
        facade = ScannerFacade(session)

        class Custom(Scanner):
            name = "custom"
            def scan(self, session, **kw):
                return [ScannerResult(instrument=MagicMock(), scanner_name="custom",
                                      score=42.0, signal="BUY")]

        results = facade.custom(Custom())
        assert len(results) == 1
        assert results[0].score == 42.0

    def test_register_and_use(self):
        session = _make_session_with_instruments()
        facade = ScannerFacade(session)

        class Tagged(Scanner):
            name = "tagged"
            def scan(self, session, **kw):
                return []

        facade.register(Tagged())
        assert "tagged" in facade._scanners

    def test_empty_universe(self):
        """All scanners return [] when no instruments are registered."""
        session = _make_session_with_instruments()
        facade = ScannerFacade(session)
        assert facade.gap() == []
        assert facade.volume() == []
        assert facade.momentum() == []
        assert facade.breakout() == []
        assert facade.imbalance() == []

    def test_results_ranked(self):
        """Results from facade entry points are ranked by score descending."""
        inst_a = _make_instrument("A", ltp=110, prev_close=100)  # 10% gap
        inst_b = _make_instrument("B", ltp=105, prev_close=100)  # 5% gap
        session = _make_session_with_instruments(inst_a, inst_b)
        facade = ScannerFacade(session)
        results = facade.gap()
        assert len(results) == 2
        assert results[0].rank == 1
        assert results[0].score > results[1].score


# ============================================================ Gap Scanner

class TestGapScanner:
    def test_gap_up_detected(self):
        inst = _make_instrument("TCS", ltp=3600, prev_close=3500)
        session = _make_session_with_instruments(inst)
        results = GapScanner().scan(session)
        assert len(results) == 1
        assert results[0].signal == "BUY"
        assert "gap_up" in results[0].matched_conditions

    def test_gap_down_detected(self):
        inst = _make_instrument("TCS", ltp=3400, prev_close=3500)
        session = _make_session_with_instruments(inst)
        results = GapScanner().scan(session)
        assert len(results) == 1
        assert results[0].signal == "SELL"
        assert "gap_down" in results[0].matched_conditions

    def test_below_threshold_ignored(self):
        inst = _make_instrument("TCS", ltp=3510, prev_close=3500)  # 0.28%
        session = _make_session_with_instruments(inst)
        results = GapScanner().scan(session, min_gap_pct=1.0)
        assert results == []

    def test_no_prev_close_skipped(self):
        inst = _make_instrument("TCS", ltp=3500, prev_close=0)
        session = _make_session_with_instruments(inst)
        assert GapScanner().scan(session) == []


# ============================================================ Volume Spike

class TestVolumeSpikeScanner:
    def test_spike_via_avg_volume(self):
        inst = _make_instrument("REL", ltp=2500, volume=500_000,
                                indicators={"avg_volume": 100_000})
        session = _make_session_with_instruments(inst)
        results = VolumeSpikeScanner().scan(session, spike_multiplier=2.0)
        assert len(results) == 1
        assert results[0].score == 5.0  # 500k / 100k

    def test_spike_via_absolute_volume(self):
        inst = _make_instrument("REL", ltp=2500, volume=200_000)
        session = _make_session_with_instruments(inst)
        results = VolumeSpikeScanner().scan(session, min_volume=100_000)
        assert len(results) == 1

    def test_below_threshold(self):
        inst = _make_instrument("REL", ltp=2500, volume=50_000)
        session = _make_session_with_instruments(inst)
        assert VolumeSpikeScanner().scan(session, min_volume=100_000) == []


# ============================================================ Momentum

class TestMomentumScanner:
    def test_rsi_momentum(self):
        inst = _make_instrument("INFY", ltp=1500, indicators={"rsi": 72.0})
        session = _make_session_with_instruments(inst)
        results = MomentumScanner().scan(session, rsi_threshold=60.0)
        assert len(results) == 1
        assert "rsi_momentum" in results[0].matched_conditions

    def test_price_change_fallback(self):
        inst = _make_instrument("INFY", ltp=1600, prev_close=1500)  # 6.67%
        session = _make_session_with_instruments(inst)
        results = MomentumScanner().scan(session, min_change_pct=1.0)
        assert len(results) == 1
        assert "price_momentum" in results[0].matched_conditions
        assert results[0].signal == "BUY"

    def test_negative_momentum(self):
        inst = _make_instrument("INFY", ltp=1400, prev_close=1500)  # -6.67%
        session = _make_session_with_instruments(inst)
        results = MomentumScanner().scan(session, min_change_pct=1.0)
        assert len(results) == 1
        assert results[0].signal == "SELL"


# ============================================================ Breakout

class TestBreakoutScanner:
    def test_supertrend_breakout(self):
        inst = _make_instrument("HDFC", ltp=1700,
                                indicators={"supertrend": 1650.0})
        session = _make_session_with_instruments(inst)
        results = BreakoutScanner().scan(session)
        assert len(results) == 1
        assert "above_supertrend" in results[0].matched_conditions

    def test_high_breakout(self):
        inst = _make_instrument("HDFC", ltp=1700, high=1700, low=1600)
        session = _make_session_with_instruments(inst)
        results = BreakoutScanner().scan(session)
        assert len(results) == 1
        assert "high_breakout" in results[0].matched_conditions
        assert results[0].signal == "BUY"

    def test_low_breakout(self):
        inst = _make_instrument("HDFC", ltp=1600, high=1700, low=1600)
        session = _make_session_with_instruments(inst)
        results = BreakoutScanner().scan(session)
        assert len(results) == 1
        assert "low_breakout" in results[0].matched_conditions
        assert results[0].signal == "SELL"

    def test_no_breakout(self):
        inst = _make_instrument("HDFC", ltp=1650, high=1700, low=1600)
        session = _make_session_with_instruments(inst)
        assert BreakoutScanner().scan(session) == []


# ============================================================ Imbalance

class TestImbalanceScanner:
    def test_bid_heavy(self):
        depth = MarketDepth(
            symbol="WIPRO",
            bids=(DepthLevel(price=400, quantity=5000),),
            asks=(DepthLevel(price=401, quantity=1000),),
        )
        inst = _make_instrument("WIPRO", ltp=400, depth=depth)
        session = _make_session_with_instruments(inst)
        results = ImbalanceScanner().scan(session, imbalance_ratio=2.0)
        assert len(results) == 1
        assert results[0].signal == "BUY"
        assert "bid_heavy" in results[0].matched_conditions

    def test_ask_heavy(self):
        depth = MarketDepth(
            symbol="WIPRO",
            bids=(DepthLevel(price=400, quantity=1000),),
            asks=(DepthLevel(price=401, quantity=5000),),
        )
        inst = _make_instrument("WIPRO", ltp=400, depth=depth)
        session = _make_session_with_instruments(inst)
        results = ImbalanceScanner().scan(session, imbalance_ratio=2.0)
        assert len(results) == 1
        assert results[0].signal == "SELL"
        assert "ask_heavy" in results[0].matched_conditions

    def test_no_depth_skipped(self):
        inst = _make_instrument("WIPRO", ltp=400)  # empty depth
        session = _make_session_with_instruments(inst)
        assert ImbalanceScanner().scan(session) == []

    def test_balanced_book_skipped(self):
        depth = MarketDepth(
            symbol="WIPRO",
            bids=(DepthLevel(price=400, quantity=1000),),
            asks=(DepthLevel(price=401, quantity=1000),),
        )
        inst = _make_instrument("WIPRO", ltp=400, depth=depth)
        session = _make_session_with_instruments(inst)
        assert ImbalanceScanner().scan(session, imbalance_ratio=2.0) == []


# ============================================================ TradingSession integration

class TestTradingSessionScanner:
    def test_scanner_returns_facade(self):
        session = _make_session_with_instruments()
        facade = session.scanner()
        assert isinstance(facade, ScannerFacade)

    def test_scanner_cached(self):
        session = _make_session_with_instruments()
        assert session.scanner() is session.scanner()

    def test_end_to_end_gap(self):
        inst_a = _make_instrument("AAA", ltp=220, prev_close=200)  # 10% gap
        inst_b = _make_instrument("BBB", ltp=102, prev_close=100)  # 2% gap
        session = _make_session_with_instruments(inst_a, inst_b)
        results = session.scanner().gap(min_gap_pct=1.0)
        assert len(results) == 2
        assert results[0].rank == 1
        assert results[0].instrument.symbol == "AAA"


def test_scanners_accept_now_parameter():
    """All built-in scanners accept an explicit now= for deterministic timestamps."""
    from datetime import datetime
    from ntrade.scanners.builtin import GapScanner, VolumeSpikeScanner, MomentumScanner, BreakoutScanner, ImbalanceScanner
    from unittest.mock import MagicMock

    session = MagicMock()
    session.kernel.ctx.instruments = {}
    ts = datetime(2026, 1, 1, 10, 0, 0)

    # All scanners should accept now= without error (even with empty instruments)
    for ScannerCls in [GapScanner, VolumeSpikeScanner, MomentumScanner, BreakoutScanner, ImbalanceScanner]:
        scanner = ScannerCls()
        results = scanner.scan(session, now=ts)
        assert results == []  # no instruments → no results
