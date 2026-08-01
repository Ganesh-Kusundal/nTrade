"""SyntheticMarketFeedSource — 1m OHLCV history extrapolated to 1-second ticks.

Deterministic offline stand-in for a live feed: turns a 1m OHLCV frame (e.g.
fetched via DhanBroker.get_historical) into one TickEvent per simulated second
using the seeded engine in ntrade.sim. Publishing a QuoteEvent per bar keeps
the instrument read-model's OHLCV consistent. Same canonical events as live —
the kernel does not know the difference (zero parity).
"""

from __future__ import annotations

import threading

from ntrade.events.market import QuoteEvent, TickEvent
from ntrade.sim.tick_simulator import synthesize_1m_ticks
from ntrade.sources.market_feed import MarketFeedSource


class SyntheticMarketFeedSource(MarketFeedSource):
    name = "synthetic"

    def __init__(self, kernel=None, *, symbol: str = "SYM", exchange: str = "NSE",
                 data=None, seed: int = 0, seconds: int = 60):
        super().__init__(kernel)
        if data is None or data.empty:
            raise ValueError("data must be a non-empty 1m OHLCV frame")
        self.symbol = symbol
        self.exchange = exchange
        self.data = data
        self.seed = seed
        self.seconds = seconds
        self.ticks_published = 0
        self._thread = None
        self._stopping = False

    # ------------------------------------------------------------------ feed
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._produce, daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self) -> None:
        self._stopping = True
        self.join(timeout=2)

    def _produce(self) -> None:
        for _, row in self.data.iterrows():
            if self._stopping:
                return
            ts = row["timestamp"]
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            open_, high, low, close = (float(row["open"]), float(row["high"]),
                                       float(row["low"]), float(row["close"]))
            volume = int(row.get("volume", 0) or 0)
            self.bus.publish(QuoteEvent(
                symbol=self.symbol, exchange=self.exchange, ltp=close, bid=0.0, ask=0.0,
                open=open_, high=high, low=low, volume=volume, ts=ts,
            ))
            for tick in synthesize_1m_ticks(ts, open_, high, low, close, volume,
                                            seed=self.seed, seconds=self.seconds):
                if self._stopping:
                    return
                if hasattr(self.kernel.clock, "set"):
                    self.kernel.clock.set(tick.ts)
                self.bus.publish(TickEvent(
                    symbol=self.symbol, exchange=self.exchange, price=tick.price,
                    quantity=tick.quantity, ts=tick.ts,
                ))
                self.ticks_published += 1
