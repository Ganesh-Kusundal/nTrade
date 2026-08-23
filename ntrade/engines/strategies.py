"""Reusable strategies built on the kernel's canonical events.

Strategies live in ``Strategy`` subclass form: they react to events through the
standard hooks and emit signals via ``emit_signal()``, so they run identically
in live (BrokerExecution) and backtest (BacktestSimulator).
"""

from __future__ import annotations

import logging
import pandas as pd


from ntrade.engines.strategy_engine import Strategy
from ntrade.registry import StrategySpec, strategy



logger = logging.getLogger("ntrade.strategy.halftrend")


class HalfTrendStrategy(Strategy):
    """HalfTrend signal strategy — emits BUY/SELL on trend flips.

    The engine-side face of the domain HalfTrend indicator. Accumulates closed
    candles, runs ``halftrend()`` over the buffer on each bar close, and emits
    a signal when the latest bar produces a buy/sell marker. The full indicator
    math lives in ``ntrade.domain.analytics.halftrend`` (zero-parity: the same
    function the chart overlay runs).
    """

    name = "halftrend"

    def __init__(self, symbol: str | None = None, exchange: str | None = None,
                 amplitude: int = 2, channel_deviation: int = 2,
                 atr_period: int = 100, lot_size: int = 1,
                 strategy_params: dict | None = None):
        super().__init__()
        self.symbol = symbol
        self.exchange = exchange
        self.lot_size = int(lot_size)
        self.amplitude = int(amplitude)
        self.channel_deviation = int(channel_deviation)
        self.atr_period = int(atr_period)
        self.strategy_params = dict(strategy_params or {})
        self._ht: list[float] = []
        self._trend: list[int] = []
        self._buy: list[bool] = []
        self._sell: list[bool] = []
        self._buf: list[dict] = []

    def _bind(self, df):
        from ntrade.domain.analytics.halftrend import halftrend as _ht
        out = _ht(df, amplitude=self.amplitude,
                  channel_deviation=self.channel_deviation,
                  atr_period=self.atr_period)
        if out.empty:
            self._ht = []
            self._trend = []
            self._buy = []
            self._sell = []
            return
        self._ht = out["ht"].tolist()
        self._trend = out["trend"].fillna(0).astype(int).tolist()
        self._buy = out["buySignal"].tolist()
        self._sell = out["sellSignal"].tolist()

    def latest_signal(self, close: float) -> dict | None:
        # Same warmup gate as the chart overlay: trend flips before atr_period
        # evolve on uninitialized ATR state — never trade them.
        if len(self._buy) < self.atr_period:
            return None
        if not self._buy or not self._sell:
            return None
        n = len(self._buy)
        if n == 0:
            return None
        idx = n - 1
        if self._buy[idx]:
            return {"side": "BUY", "price": float(close),
                    "ht": float(self._ht[idx]) if self._ht[idx] == self._ht[idx] else None}
        if self._sell[idx]:
            return {"side": "SELL", "price": float(close),
                    "ht": float(self._ht[idx]) if self._ht[idx] == self._ht[idx] else None}
        return None

    def on_candle_closed(self, event) -> None:
        if self.symbol is not None and event.symbol != self.symbol:
            return
        if getattr(event, "timeframe", "1m") != "1m":
            return  # mixed-timeframe streams would corrupt the bar buffer
        self._buf.append({
            "open": float(event.open),
            "high": float(event.high),
            "low": float(event.low),
            "close": float(event.close),
            "volume": float(event.volume),
        })
        self._bind(pd.DataFrame(self._buf))
        sig = self.latest_signal(float(event.close))
        if sig is None:
            return
        self.emit_signal(
            symbol=event.symbol,
            exchange=event.exchange,
            side=sig["side"],
            quantity=self.lot_size,
            price=float(event.close),
            reference_price=float(event.close),
            order_type="MARKET",
            ht=sig.get("ht"),
        )




def _build_halftrend_overlay(df: pd.DataFrame, params: dict | None) -> dict | None:
    """Chart overlay markers for HalfTrend — mirrors the strategy's signal math.

    The paper/live path emits signals from ``HalfTrendStrategy``; this function
    produces the same buy/sell markers from the same domain ``halftrend()`` math
    so the chart overlay agrees with the kernel (zero-parity). Registered on the
    spec so ``build_overlays`` is registry-driven, not hardcoded per id.
    """
    if df is None or df.empty:
        return None
    from ntrade.domain.analytics.halftrend import halftrend as _ht
    amp = int(params.get("amplitude", 2)) if params else 2
    cdev = int(params.get("channel_deviation", 2)) if params else 2
    apt = int(params.get("atr_period", 100)) if params else 100
    out = _ht(df, amplitude=amp, channel_deviation=cdev, atr_period=apt)
    if out.empty:
        return None
    markers = []
    # ponytail: skip ATR warmup — trend flips before atr_period are on
    # uninitialized state and produce spurious signals
    for i in range(apt, len(out)):
        row = out.iloc[i]
        if pd.notna(row.get("buySignal")) and bool(row["buySignal"]):
            markers.append({
                "index": int(i),
                "time": int(df["time"].iloc[i]),
                "side": "BUY",
                "price": round(float(df["close"].iloc[i]), 4),
                "ht": round(float(row["ht"]), 4) if pd.notna(row["ht"]) else None,
                "trend": int(row["trend"]),
            })
        elif pd.notna(row.get("sellSignal")) and bool(row["sellSignal"]):
            markers.append({
                "index": int(i),
                "time": int(df["time"].iloc[i]),
                "side": "SELL",
                "price": round(float(df["close"].iloc[i]), 4),
                "ht": round(float(row["ht"]), 4) if pd.notna(row["ht"]) else None,
                "trend": int(row["trend"]),
            })
    return {
        "id": "halftrend",
        "markers": markers,
        "series": {
            "ht": [round(float(x), 4) if pd.notna(x) else None
                   for x in out["ht"].tolist()],
            "trend": [int(x) for x in out["trend"].tolist()],
            "atrHigh": [round(float(x), 4) if pd.notna(x) else None
                        for x in out["atrHigh"].tolist()],
            "atrLow": [round(float(x), 4) if pd.notna(x) else None
                       for x in out["atrLow"].tolist()],
        },
    }


# Registration: spec carries the factory (turns id → instance) and optional
# overlay builder. This is the ONLY place a strategy is wired — backtest,
# replay, live, paper and the chart all resolve it through `strategy.instantiate`
# / `spec.overlay_fn`, so adding a strategy is one register() call (plus the
# class itself). No call-site edits elsewhere.
strategy.register("halftrend", StrategySpec(
    id="halftrend", label="HalfTrend",
    params={"amplitude": 2, "channel_deviation": 2, "atr_period": 100,
            "symbol": None, "exchange": None},
    indicators=["halftrend"],
    factory=HalfTrendStrategy,
    overlay_fn=_build_halftrend_overlay,
))

# ORB+VWAP was already implemented but never registered — without registration
# it was invisible to the registry loader, the catalog API, and the chart.
# Registering it closes the doc/code gap (it is referenced in user guides).
from ntrade.engines.orb_vwap import OpeningRangeBreakout  # noqa: E402

strategy.register("orb_vwap", StrategySpec(
    id="orb_vwap", label="Opening Range Breakout + VWAP",
    params={"symbol": None, "exchange": "NFO", "timeframe": "1m",
            "risk_per_trade_pct": 1.0, "lot_size": 1, "session_start": "09:15",
            "orb_end": "09:30", "entry_end": "10:30", "ema_fast": 9,
            "ema_slow": 21, "volume_mult": 1.5, "target_rr": 2.0,
            "sl_buffer": 0.0, "max_trades_per_day": 2},
    indicators=["vwap", "ema"],
    factory=OpeningRangeBreakout,
    # ORB trades the morning window against live VWAP/EMA state — the chart
    # overlay is the same indicator series the strategy consumes, not a
    # separate signal replay, so no custom overlay_fn is needed.
))