"""IndicatorEngine — recomputes the indicator bundle on every candle close.

Consumes CandleClosedEvent, maintains a rolling OHLCV window per symbol, runs
``compute_bundle`` from the existing domain analytics, projects the result into
the instrument (read model) and broadcasts IndicatorUpdatedEvent.
"""

from __future__ import annotations

import pandas as pd

from ntrade.domain.analytics.indicators import compute_bundle
from ntrade.events.market import CandleClosedEvent, IndicatorUpdatedEvent

_MIN_ROWS = 10


class IndicatorEngine:
    def __init__(self, context, timeframe: str = "1m", *, max_rows: int = 1_000, **params):
        self.ctx = context
        self.timeframe = timeframe
        self.params = params
        self._rows: dict[str, list[dict]] = {}
        self._latest: dict[str, dict] = {}
        self._max_rows = max_rows
        context.bus.subscribe(CandleClosedEvent, self.on_candle_closed)

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
        bundle = compute_bundle(frame, **self.params)
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
