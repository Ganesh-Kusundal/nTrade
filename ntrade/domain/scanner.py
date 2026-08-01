"""Scanner subsystem — pluggable market scanners for the TradeXV2 SDK.

A *scanner* inspects a universe of instruments and returns ranked results
with a signal (BUY / SELL / NEUTRAL), a score, and matched conditions.
The ``ScannerFacade`` provides named access to built-in scanners and
supports user-defined custom scanners.

Example::

    session = TradingSession.paper()
    results = session.scanner().breakout(min_volume=100_000)
    for r in results:
        print(f"{r.instrument.symbol}: {r.signal} score={r.score:.2f}")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument
    from ntrade.kernel.trading_session import TradingSession


@dataclass(frozen=True)
class ScannerResult:
    """One instrument's scan result.

    Always carries an ``Instrument`` (never a raw symbol string) so callers
    can immediately access market data, analytics, or place orders.
    """

    instrument: "Instrument"
    scanner_name: str
    score: float
    signal: str  # "BUY" | "SELL" | "NEUTRAL"
    matched_conditions: tuple[str, ...] = ()
    indicator_values: dict[str, float] = field(default_factory=dict)
    rank: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = None


class Scanner(ABC):
    """Abstract base for all scanners.

    Subclasses implement ``scan()`` which receives the active
    ``TradingSession`` and returns a list of ``ScannerResult``.
    """

    name: str = "base"

    @abstractmethod
    def scan(self, session: "TradingSession", **kw: Any) -> list[ScannerResult]:
        """Run the scan and return ranked results."""
        ...

    def top(self, session: "TradingSession", n: int = 10, **kw: Any) -> list[ScannerResult]:
        """Convenience: run scan and return top *n* results by score."""
        results = self.scan(session, **kw)
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:n]):
            object.__setattr__(r, "rank", i + 1)
        return results[:n]


class ScannerFacade:
    """Named access to built-in and custom scanners.

    Constructed internally by ``TradingSession.scanner()``.
    """

    def __init__(self, session: "TradingSession"):
        self._session = session
        self._scanners: dict[str, Scanner] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        from ntrade.scanners.builtin import (
            BreakoutScanner,
            GapScanner,
            ImbalanceScanner,
            MomentumScanner,
            VolumeSpikeScanner,
        )
        for cls in (GapScanner, VolumeSpikeScanner, MomentumScanner,
                    BreakoutScanner, ImbalanceScanner):
            self._scanners[cls.name] = cls()

    # ---- named entry points ------------------------------------------------

    def gap(self, **kw: Any) -> list[ScannerResult]:
        """Run the gap scanner."""
        scanner = self._scanners["gap"]
        return self._run(scanner, **kw)

    def volume(self, **kw: Any) -> list[ScannerResult]:
        """Run the volume-spike scanner."""
        scanner = self._scanners["volume_spike"]
        return self._run(scanner, **kw)

    def momentum(self, **kw: Any) -> list[ScannerResult]:
        """Run the momentum scanner."""
        scanner = self._scanners["momentum"]
        return self._run(scanner, **kw)

    def breakout(self, **kw: Any) -> list[ScannerResult]:
        """Run the breakout scanner."""
        scanner = self._scanners["breakout"]
        return self._run(scanner, **kw)

    def imbalance(self, **kw: Any) -> list[ScannerResult]:
        """Run the order-imbalance scanner."""
        scanner = self._scanners["imbalance"]
        return self._run(scanner, **kw)

    def custom(self, scanner: Scanner, **kw: Any) -> list[ScannerResult]:
        """Run a user-defined scanner."""
        return self._run(scanner, **kw)

    def register(self, scanner: Scanner) -> "ScannerFacade":
        """Register a custom scanner for later use."""
        self._scanners[scanner.name] = scanner
        return self

    # ---- internal ----------------------------------------------------------

    def _run(self, scanner: Scanner, **kw: Any) -> list[ScannerResult]:
        results = scanner.scan(self._session, **kw)
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results):
            object.__setattr__(r, "rank", i + 1)
        return results
