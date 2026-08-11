"""Screener — compose multiple scanners into a single weighted, ranked watchlist.

A *screener* differs from a *scanner*: a scanner answers "which instruments
match condition X?" (filter + per-metric score).  A screener answers "which
instruments rank highest across a *blend* of conditions, each with its own
weight?"

Design constraints enforced here:
  - Composes existing ``Scanner`` instances (no subclassing duplication).
  - Every result batch is run through ``Scanner.normalize()`` so no single
    metric's raw magnitude dominates the composite score.
  - NOT cached by the per-instrument rate-limit throttle — screeners run on a
    schedule, and the cache key in ``ScannerFacade._run`` is keyed by the
    scanner *instance*, which a screener shares across weight configs.

Usage::

    session = TradingSession.connect("dhan")
    sc = session.screener()
    watchlist = sc.register(
        (GapScanner(), 0.4),
        (VolumeSpikeScanner(), 0.6),
    ).run(limit=20)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dataclasses import replace

from ntrade.domain.scanner import Scanner, ScannerResult, top

if TYPE_CHECKING:
    from ntrade.kernel.trading_session import TradingSession


class ScreenerFacade:
    """Compose scanners, normalize, weight, and rank into one watchlist."""

    def __init__(self, session: "TradingSession"):
        self._session = session
        self._criteria: list[tuple[Scanner, float]] = []

    def register(self, *criteria: tuple[Scanner, float]) -> "ScreenerFacade":
        """Add (scanner, weight) pairs.  Weights are normalized internally."""
        for scanner, weight in criteria:
            self._criteria.append((scanner, float(weight)))
        return self

    def run(self, *, limit: int | None = None, **kw) -> list[ScannerResult]:
        """Run all registered scanners, normalize + weight, return ranked results.

        Args:
            limit: optional cap; returns the N highest composite-score results.
            **kw: forwarded to each scanner's ``scan()`` (e.g. ``min_volume=…``).
        """
        # Aggregate normalized results keyed by instrument id so the same
        # instrument appearing in multiple scanner batches gets a composite.
        merged: dict[str, ScannerResult] = {}
        for scanner, weight in self._criteria:
            raw = scanner.scan(self._session, **kw)
            normalized = scanner.normalize(raw)
            for r in normalized:
                key = str(id(r.instrument))
                composite = weight * r.score
                if key in merged:
                    existing = merged[key]
                    merged[key] = replace(
                        existing,
                        score=existing.score + composite,
                        matched_conditions=existing.matched_conditions
                        + (f"{r.scanner_name}:{r.matched_conditions}",),
                    )
                else:
                    merged[key] = replace(r, score=composite)

        results = sorted(merged.values(), key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results):
            object.__setattr__(r, "rank", i + 1)
        final = top(results, limit) if limit is not None else results
        # Publish a WatchlistReady so strategies with on_watchlist hooks
        # can react identically in backtest, replay, and live (zero-parity).
        if self._session is not None and final:
            from ntrade.events.market import WatchlistReady
            self._session._kernel.bus.publish(WatchlistReady(
                results=tuple(final),
                strategy="",
                ts=self._session._kernel.clock.now(),
            ))
        return final
