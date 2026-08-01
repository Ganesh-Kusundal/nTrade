"""CandleEngine — aggregates ticks into candles and emits CandleClosedEvent.

One engine per timeframe (1m, 5m, ...). A candle closes when a tick arrives in
a later bucket; the closed candle is broadcast for indicators/strategies. The
last partial candle can be flushed explicitly at session end.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ntrade.events.market import CandleClosedEvent, TickEvent

_INTERVAL_SECONDS = {
    "1s": 1, "5s": 5, "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400,
}


class CandleEngine:
    def __init__(self, context, timeframe: str = "1m", *, max_candles: int = 10_000):
        if timeframe not in _INTERVAL_SECONDS:
            raise ValueError(f"Unsupported timeframe {timeframe!r}; expected one of {sorted(_INTERVAL_SECONDS)}")
        self.ctx = context
        self.timeframe = timeframe
        self.seconds = _INTERVAL_SECONDS[timeframe]
        self._open: dict[str, dict] = {}
        self._closed: dict[str, list[CandleClosedEvent]] = {}
        self._max_candles = max_candles
        context.bus.subscribe(TickEvent, self.on_tick)

    # ------------------------------------------------------------------ ingest
    def _bucket(self, ts: datetime) -> int:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)  # naive -> UTC-pinned epoch
        epoch = int(ts.timestamp())
        return epoch - (epoch % self.seconds)

    def on_tick(self, event: TickEvent) -> None:
        self._ingest(event.symbol, event.exchange, event.price, event.ts, volume=event.quantity)

    def _ingest(self, symbol, exchange, price, ts, volume: int = 0) -> None:
        bucket = self._bucket(ts)
        candle = self._open.get(symbol)
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
