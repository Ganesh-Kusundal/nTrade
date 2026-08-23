"""IndicatorEngine — recomputes the indicator bundle on every candle close.

Consumes CandleClosedEvent, maintains a rolling OHLCV window per symbol, runs
``compute_bundle`` from the existing domain analytics, projects the result into
the instrument (read model) and broadcasts IndicatorUpdatedEvent.
"""

from __future__ import annotations

import pandas as pd

from ntrade.domain.analytics.indicators import compute_bundle
from ntrade.domain.constants import DEFAULT_TIMEFRAME
from ntrade.events.market import CandleClosedEvent, IndicatorUpdatedEvent

_MIN_ROWS = 10


class IndicatorEngine:
    def __init__(self, context, timeframe: str = DEFAULT_TIMEFRAME, *, max_rows: int = 1_000, **params):
        self.ctx = context
        self.timeframe = timeframe
        self.params = params
        # None = compute the default bundle; a tuple restricts to the named
        # indicator ids (e.g. the ones a registered strategy reads).
        self._indicators: tuple[str, ...] | None = None
        self._rows: dict[str, list[dict]] = {}
        self._latest: dict[str, dict] = {}
        self._max_rows = max_rows
        context.bus.subscribe(CandleClosedEvent, self.on_candle_closed)

    def set_indicators(self, ids: tuple[str, ...] | None) -> None:
        """Restrict the computed bundle to ``ids`` (or reset to default with None)."""
        self._indicators = tuple(ids) if ids else None

    def warm_up(self, symbol: str, rows: list[dict]) -> None:
        """Seed the rolling OHLCV window from historical bars so live
        decisions aren't delayed by the cold-start warm-up. Rows carry
        open/high/low/close/volume (naive IST wire rows)."""
        buf = self._rows.setdefault(symbol, [])
        for r in rows:
            buf.append({
                "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
            })
        if len(buf) > self._max_rows:
            del buf[:len(buf) - self._max_rows]

    def on_candle_closed(self, event: CandleClosedEvent) -> None:
        if event.timeframe != self.timeframe:
            return
        rows = self._rows.setdefault(event.symbol, [])
        rows.append({
            "open": event.open, "high": event.high,
            "low": event.low, "close": event.close, "volume": event.volume,
        })
        if len(rows) > self._max_rows:
            del rows[:len(rows) - self._max_rows]
        if len(rows) < _MIN_ROWS:
            return
        frame = pd.DataFrame(rows[-500:])
        bundle = compute_bundle(frame, indicators=self._indicators, **self.params)
        if not bundle:
            return
        instrument = self.ctx.instrument(event.symbol)
        if instrument is not None:
            instrument._indicators.update(bundle)
        self._latest[event.symbol] = bundle
        self.ctx.bus.publish(IndicatorUpdatedEvent(
            symbol=event.symbol, exchange=event.exchange, timeframe=self.timeframe,
            indicators=bundle, ts=event.ts,
        ))

    def latest(self, symbol: str) -> dict:
        return dict(self._latest.get(symbol, {}))
