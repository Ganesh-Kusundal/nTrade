"""MarketEngine — projects raw market events into instrument read-model state.

Consumes TickEvent / QuoteEvent / DepthEvent from any source (live broker,
replay file, simulator) and broadcasts QuoteUpdatedEvent so downstream engines
react to a single normalized stream.
"""

from __future__ import annotations

from ntrade.domain.market.depth import DepthLevel, MarketDepth
from ntrade.domain.market.quote import Quote, Tick
from ntrade.events.market import DepthEvent, QuoteEvent, QuoteUpdatedEvent, TickEvent


class MarketEngine:
    def __init__(self, context):
        self.ctx = context
        context.bus.subscribe(TickEvent, self.on_tick)
        context.bus.subscribe(QuoteEvent, self.on_quote)
        context.bus.subscribe(DepthEvent, self.on_depth)

    def _instrument(self, symbol: str):
        return self.ctx.instrument(symbol)

    def on_tick(self, event: TickEvent) -> None:
        instrument = self._instrument(event.symbol)
        if instrument is None:
            return
        tick = Tick(
            symbol=event.symbol, price=event.price, quantity=event.quantity,
            side=event.side, timestamp=event.ts, kind=event.kind,
        )
        instrument._stream.ingest_tick(tick)
        self.ctx.bus.publish(QuoteUpdatedEvent(
            symbol=event.symbol, exchange=event.exchange,
            ltp=event.price, bid=instrument.market.bid(), ask=instrument.market.ask(), ts=event.ts,
        ))

    def on_quote(self, event: QuoteEvent) -> None:
        instrument = self._instrument(event.symbol)
        if instrument is None:
            return
        instrument.apply_quote(Quote(
            ltp=event.ltp, bid=event.bid, ask=event.ask, open=event.open,
            high=event.high, low=event.low, prev_close=event.prev_close,
            volume=event.volume, oi=event.oi, timestamp=event.ts,
        ))
        self.ctx.bus.publish(QuoteUpdatedEvent(
            symbol=event.symbol, exchange=event.exchange,
            ltp=event.ltp, bid=event.bid, ask=event.ask, ts=event.ts,
        ))

    def on_depth(self, event: DepthEvent) -> None:
        instrument = self._instrument(event.symbol)
        if instrument is None:
            return
        depth = MarketDepth(
            symbol=event.symbol,
            bids=tuple(DepthLevel(*row) for row in event.bids) if event.bids else (),
            asks=tuple(DepthLevel(*row) for row in event.asks) if event.asks else (),
            timestamp=event.ts,
        )
        instrument.apply_depth(depth)
