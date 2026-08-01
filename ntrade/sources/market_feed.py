"""MarketFeedSource — the event-source abstraction of the zero-parity kernel.

A source produces canonical market events (TickEvent/QuoteEvent/DepthEvent)
and feeds them into a TradingKernel's bus. Live websockets, replay files,
simulators and historical bars are all interchangeable sources — the kernel
never knows where events came from, which is the zero-parity invariant.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from ntrade.events.market import QuoteEvent, TickEvent

if TYPE_CHECKING:
    from ntrade.kernel.session import TradingKernel


class MarketFeedSource(ABC):
    """Produces canonical market events for a kernel."""

    name = "source"

    def __init__(self, kernel: "TradingKernel | None" = None):
        self.kernel = kernel

    def attach(self, kernel: "TradingKernel") -> "MarketFeedSource":
        self.kernel = kernel
        return self

    @property
    def bus(self):
        return self.kernel.bus if self.kernel is not None else None

    @abstractmethod
    def start(self) -> None:
        """Begin producing events (blocking or background)."""

    def stop(self) -> None:
        """Stop producing events (no-op by default)."""


class SimulatedFeedSource(MarketFeedSource):
    """Deterministic tick source from a price path or an OHLCV frame.

    Offline stand-in for a live feed: publishes the same canonical events a
    broker websocket would, so kernels and strategies run identically in
    simulation, replay and live (zero parity).
    """

    name = "simulated"

    def __init__(self, kernel=None, *, symbol: str = "SIM", exchange: str = "NSE",
                 prices: list[float] | None = None, data: pd.DataFrame | None = None,
                 start_ts: datetime | None = None):
        super().__init__(kernel)
        if prices is None and data is None:
            raise ValueError("provide either 'prices' (float path) or 'data' (OHLCV frame)")
        self.symbol = symbol
        self.exchange = exchange
        self.prices = prices
        self.data = data
        self.start_ts = start_ts or datetime(2026, 1, 1, 9, 15)
        self.ticks_published = 0

    # ------------------------------------------------------------------ feed
    def start(self) -> None:
        if self.data is not None:
            self._feed_frame()
        else:
            self._feed_prices()

    def _feed_prices(self) -> None:
        for i, price in enumerate(self.prices):
            ts = self.start_ts + timedelta(seconds=i)
            if hasattr(self.kernel.clock, "set"):
                self.kernel.clock.set(ts)
            self.bus.publish(TickEvent(
                symbol=self.symbol, exchange=self.exchange,
                price=float(price), ts=ts,
            ))
            self.ticks_published += 1

    def _feed_frame(self) -> None:
        for _, row in self.data.iterrows():
            ts = row["timestamp"]
            close = float(row.get("close", 0))
            if hasattr(self.kernel.clock, "set"):
                self.kernel.clock.set(ts)
            self.bus.publish(QuoteEvent(
                symbol=self.symbol, exchange=self.exchange, ltp=close, bid=0.0, ask=0.0,
                open=float(row.get("open", close)), high=float(row.get("high", close)),
                low=float(row.get("low", close)),
                volume=int(row.get("volume", 0) or 0), ts=ts,
            ))
            self.bus.publish(TickEvent(
                symbol=self.symbol, exchange=self.exchange, price=close,
                quantity=int(row.get("volume", 0) or 0), ts=ts,
            ))
            self.ticks_published += 1
