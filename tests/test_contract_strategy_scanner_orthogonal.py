"""Graph-aware contract (Item E): strategy registration is orthogonal to the
scanner throttle.

Registering a strategy must never reset ``ScannerFacade``'s rate-limit cache.
A hot scanner that was just warmed keeps serving its cached rank from the same
``_run`` call even after a strategy is registered into the ``StrategyEngine``.
"""

from unittest.mock import MagicMock

from ntrade.domain.scanner import ScannerResult
from ntrade.engines.strategy_engine import Strategy
from ntrade.scanners.builtin import MomentumScanner


class CountingMomentum(MomentumScanner):
    """Momentum scanner that counts how many times it actually re-scans."""

    def __init__(self):
        super().__init__()
        self.scan_calls = 0

    def scan(self, session, **kw):
        self.scan_calls += 1
        return [ScannerResult(instrument=MagicMock(), scanner_name="momentum",
                              score=1.0, signal="BUY")]


class DummyStrategy(Strategy):
    name = "dummy"


def _session_with_instrument():
    from ntrade.kernel.trading_session import TradingSession
    session = TradingSession.paper()
    session._kernel.ctx.instruments["RELIANCE"] = MagicMock(symbol="RELIANCE")
    return session


def test_strategy_registration_and_scanner_throttle_are_orthogonal():
    session = _session_with_instrument()
    facade = session.scanner()
    kernel = session.kernel

    # The momentum scanner is armed to a 30s throttle (T-017).
    assert MomentumScanner.rate_limit_seconds == 30.0

    # Fill the facade's throttle cache with a counting scanner warmed once.
    counting = CountingMomentum()
    facade.register(counting)          # replaces the builtin "momentum"
    first = facade.momentum()          # warm: real scan, cached
    warmed_calls = counting.scan_calls
    assert warmed_calls == 1

    # Register a real strategy through the kernel's StrategyEngine.
    kernel.register_strategy(DummyStrategy())

    # (a) the strategy is registered ...
    assert kernel.strategy_engine.names() == ["dummy"]

    # (b) registration did NOT reset the scanner throttle: the cache still
    # serves the same top set from the warm call WITHOUT re-scanning.
    second = facade.momentum()
    assert second == first                       # same cached top set
    assert counting.scan_calls == warmed_calls   # no re-scan happened