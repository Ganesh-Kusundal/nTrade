"""Market-data events — produced by brokers/replay/simulators, consumed by engines."""

from __future__ import annotations

from dataclasses import dataclass, field

from ntrade.events.base import Event


@dataclass(frozen=True, kw_only=True)
class TickEvent(Event):
    """A single trade/quote/depth print from an exchange or replay source."""

    symbol: str
    exchange: str
    price: float
    quantity: int = 0
    side: str = ""            # "buy" | "sell" | ""
    kind: str = "trade"       # trade | quote | depth


@dataclass(frozen=True, kw_only=True)
class QuoteEvent(Event):
    """A full market quote snapshot."""

    symbol: str
    exchange: str
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    prev_close: float = 0.0
    volume: int = 0
    oi: int = 0


@dataclass(frozen=True, kw_only=True)
class DepthEvent(Event):
    """An order-book snapshot (bids/asks as (price, qty, orders) tuples)."""

    symbol: str
    exchange: str
    bids: tuple = ()
    asks: tuple = ()


@dataclass(frozen=True, kw_only=True)
class CandleClosedEvent(Event):
    """A completed candle of `timeframe` (produced by the CandleEngine)."""

    symbol: str
    exchange: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, kw_only=True)
class QuoteUpdatedEvent(Event):
    """Broadcast after the MarketEngine projects a tick/quote into an instrument."""

    symbol: str
    exchange: str
    ltp: float
    bid: float = 0.0
    ask: float = 0.0


@dataclass(frozen=True, kw_only=True)
class IndicatorUpdatedEvent(Event):
    """A fresh indicator bundle over the latest completed candle."""

    symbol: str
    exchange: str
    timeframe: str
    indicators: dict = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class WatchlistReady(Event):
    """Published when a screener run completes.

    Carries the ranked ``ScannerResult`` list so strategies can react to
    fresh watchlists via ``on_watchlist`` — works identically in backtest,
    replay, and live (zero-parity).
    """

    results: tuple = ()
    strategy: str = ""
