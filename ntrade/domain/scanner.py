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
from dataclasses import dataclass, field, replace
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

    **Score contract:** ``score`` is a scanner-local metric (e.g. gap %, RSI,
    volume ratio).  It is **not** comparable across scanner types.  Before
    combining results from different scanners, run each batch through
    ``Scanner.normalize()``.
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


def top(results: list[ScannerResult], n: int) -> list[ScannerResult]:
    """Return the ``n`` highest-scoring results (already ranked by the
    facade).  Zero-alloc slice — no re-sort."""
    return results[:n]


class Scanner(ABC):
    """Abstract base for all scanners.

    Subclasses implement ``scan()`` which receives the active
    ``TradingSession`` and returns a list of ``ScannerResult``.  Ranking and
    rate-limit throttling are handled by ``ScannerFacade._run`` (the canonical
    path); ``scan()`` itself returns raw, unranked results.
    """

    name: str = "base"
    #: Minimum seconds between actual re-scans when driven through the facade
    #: (M6). 0 = always re-scan (default, backward compatible).
    rate_limit_seconds: float = 0.0

    @abstractmethod
    def scan(self, session: "TradingSession", **kw: Any) -> list[ScannerResult]:
        """Run the scan and return raw results (unranked)."""
        ...

    def normalize(self, results: list[ScannerResult]) -> list[ScannerResult]:
        """Map each result's raw ``score`` into a 0–1 band so that a
        ``ScreenerFacade`` composing multiple scanners never lets one
        metric's magnitude (e.g. volume ratio) drown out another (e.g. gap
        pct).

        Default: min-max rescale within the batch.  Override when raw scores
        are already comparable or when a domain-specific transform is needed.
        """
        if not results:
            return results
        scores = [r.score for r in results]
        lo, hi = min(scores), max(scores)
        if hi == lo:
            return [replace(r, score=1.0) for r in results]
        span = hi - lo
        return [replace(r, score=(r.score - lo) / span) for r in results]


class ScannerFacade:
    """Named access to built-in and custom scanners.

    Constructed internally by ``TradingSession.scanner()``.
    """

    def __init__(self, session: "TradingSession"):
        self._session = session
        self._scanners: dict[str, Scanner] = {}
        self._last_run: dict[tuple, datetime] = {}
        self._cached: dict[tuple, list[ScannerResult]] = {}
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
        """Run a scanner, honouring its ``rate_limit_seconds`` throttle (M6).

        Within the rate-limit window the previous results are served from
        cache (a hot loop must not re-scan the whole universe every tick);
        an explicit ``now=`` keeps the throttle deterministic for tests.
        """
        limit = kw.pop("rate_limit_seconds", getattr(scanner, "rate_limit_seconds", 0.0)) or 0.0
        now = kw.get("now") or datetime.now()
        # Cache is keyed by the scanner *instance* AND the call parameters —
        # two distinct scanners sharing a name never share results, and a
        # parameterized scan (e.g. ``momentum(min_score=…)`` with different
        # args) never serves results computed for other arguments (M6). ``now``
        # and the throttle knob are excluded: ``now`` is the throttle clock
        # itself (it differs every tick by design), not a scan parameter.
        params = tuple(sorted((k, repr(v)) for k, v in kw.items()
                              if k not in ("now", "rate_limit_seconds")))
        key = (scanner, params)
        if limit > 0:
            last = self._last_run.get(key)
            if last is not None and (now - last).total_seconds() < limit:
                return list(self._cached.get(key, []))
        results = scanner.scan(self._session, **kw)
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results):
            object.__setattr__(r, "rank", i + 1)
        if limit > 0:
            self._last_run[key] = now
            self._cached[key] = list(results)
        return results
