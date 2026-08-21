"""CandleEngine — aggregates ticks into candles and emits CandleClosedEvent.

One engine per timeframe (1m, 5m, ...). A candle closes when a tick arrives in
a later bucket; the closed candle is broadcast for indicators/strategies. The
last partial candle can be flushed explicitly at session end.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ntrade.domain.constants import Timeframe, DEFAULT_TIMEFRAME
from ntrade.domain.market_hours import IST
from ntrade.events.market import CandleClosedEvent, QuoteEvent, TickEvent

_INTERVAL_SECONDS = {
    Timeframe.S1: 1, Timeframe.S5: 5, Timeframe.MIN: 60, Timeframe.T5: 300,
    Timeframe.T15: 900, Timeframe.H1: 3600, Timeframe.D1: 86400,
}


class CandleEngine:
    def __init__(self, context, timeframe: str = DEFAULT_TIMEFRAME, *, max_candles: int = 10_000, mode: str = "live"):
        if timeframe not in _INTERVAL_SECONDS:
            raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {sorted(_INTERVAL_SECONDS)}")
        self.ctx = context
        self.timeframe = timeframe
        self.seconds = _INTERVAL_SECONDS[timeframe]
        self._open: dict[str, dict] = {}
        self._closed: dict[str, list[CandleClosedEvent]] = {}
        self._max_candles = max_candles
        # last bucket seeded from a bar-shaped QuoteEvent (backtest only); the
        # paired close tick sharing that bucket is skipped to avoid double volume
        self._bar_seeded: dict[str, int] = {}
        self._mode = mode
        self.late_ticks = 0  # dropped out-of-order ticks (ops/tests)
        context.bus.subscribe(TickEvent, self.on_tick)
        context.bus.subscribe(QuoteEvent, self.on_quote)

    # ------------------------------------------------------------------ ingest
    def _bucket(self, ts: datetime) -> int:
        if ts.tzinfo is None:
            # Naive timestamps are IST wall time (the storage + backtest
            # convention). Convert once here — this is the single tz boundary:
            # everything downstream sees UTC-epoch buckets regardless of the
            # event source (live IST-aware ticks, naive-IST history bars).
            # Pin the zone BEFORE timestamp(): timestamp() on a naive value
            # re-reads the process-local TZ (double conversion).
            ts = ts.replace(tzinfo=IST)
        epoch = int(ts.timestamp())  # aware -> absolute, host-TZ independent
        return epoch - (epoch % self.seconds)

    def on_tick(self, event: TickEvent) -> None:
        # In backtest mode a bar's QuoteEvent is the authoritative volume unit
        # for its bucket; the paired close tick (and any extra same-bucket
        # ticks from a future multi-trade backtest source) is intentionally
        # skipped so volume is not double-counted.
        if self._mode == "backtest" and self._bar_seeded.get(event.symbol) == self._bucket(event.ts):
            return
        self._ingest(event.symbol, event.exchange, event.price, event.ts, volume=event.quantity)

    def on_quote(self, event: QuoteEvent) -> None:
        if self._mode != "backtest":
            return  # live/replay quotes carry day-session OHLCV, not bars
        self._ingest_bar(event.symbol, event.exchange, event.open, event.high,
                         event.low, event.ltp, event.volume, event.ts)

    def _ingest_bar(self, symbol, exchange, open_, high, low, close, volume, ts) -> None:
        bucket = self._bucket(ts)
        candle = self._open.get(symbol)
        if candle is None or candle["bucket"] != bucket:
            if candle is not None:
                self._close(symbol, candle)
            candle = self._open[symbol] = {
                "bucket": bucket, "exchange": exchange,
                "open": open_, "high": high, "low": low, "close": close, "volume": 0,
            }
        candle["open"], candle["high"] = open_, high
        candle["low"], candle["close"] = low, close
        candle["volume"] = volume
        self._bar_seeded[symbol] = bucket

    def _ingest(self, symbol, exchange, price, ts, volume: int = 0) -> None:
        bucket = self._bucket(ts)
        candle = self._open.get(symbol)
        if candle is not None and bucket < candle["bucket"]:
            # P0-2 parity: a late/reordered tick must not close the live
            # candle and fork the series (the API pump already guards this;
            # the kernel now matches).
            self.late_ticks += 1
            return
        if candle is None or candle["bucket"] != bucket:
            if candle is not None:
                self._close(symbol, candle)
            candle = self._open[symbol] = {
                "bucket": bucket, "exchange": exchange,
                "open": price, "high": price, "low": price, "close": price, "volume": 0,
            }
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price
        candle["volume"] += volume

    def _close(self, symbol: str, candle: dict) -> None:
        closed = CandleClosedEvent(
            symbol=symbol, exchange=candle["exchange"], timeframe=self.timeframe,
            open=candle["open"], high=candle["high"], low=candle["low"],
            close=candle["close"], volume=candle["volume"],
            # naive UTC wall-clock label, host-timezone independent
            ts=datetime.fromtimestamp(candle["bucket"] + self.seconds,
                                      tz=timezone.utc).replace(tzinfo=None),
        )
        buf = self._closed.setdefault(symbol, [])
        buf.append(closed)
        if len(buf) > self._max_candles:
            del buf[:len(buf) - self._max_candles]
        self.ctx.bus.publish(closed)

    # ------------------------------------------------------------------ query
    def candles(self, symbol: str, limit: int | None = None) -> list[CandleClosedEvent]:
        rows = self._closed.get(symbol, [])
        return list(rows[-limit:]) if limit else list(rows)

    def flush(self, symbol: str | None = None) -> None:
        """Close any open partial candles (session/backtest end)."""
        for sym in (list(self._open) if symbol is None else [symbol]):
            candle = self._open.pop(sym, None)
            if candle is not None:
                self._close(sym, candle)
