"""Stock Selector + Signal Scanner combo.

Workflow:
1. PRE-MARKET (09:00-09:45): StockSelector scans all symbols, ranks by
   gap + volume + VWAP + EMA alignment, returns top-N watchlist.
2. INTRADAY (09:45-10:30): SignalScanner monitors only the watchlist,
   emits signals when entry conditions are met.

This separates the "what to watch" problem from the "when to enter" problem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from ntrade.domain.analytics.indicators import ema, vwap
from ntrade.domain.market_hours import IST as _IST

logger = logging.getLogger("ntrade.screener")


@dataclass
class StockScore:
    """A stock's pre-market score for ranking."""
    symbol: str
    gap_pct: float = 0.0          # (open - prev_close) / prev_close
    volume_ratio: float = 0.0     # current vol / avg vol
    price: float = 0.0
    prev_close: float = 0.0
    open_price: float = 0.0
    turnover: float = 0.0         # price * volume (liquidity proxy)
    ema_aligned: bool = False     # EMA(9) > EMA(21)
    vwap_bias: float = 0.0        # (price - vwap) / vwap
    score: float = 0.0            # composite score

    def __repr__(self):
        return (f"{self.symbol}: gap={self.gap_pct:.2%} vol={self.volume_ratio:.1f}x "
                f"score={self.score:.3f}")


@dataclass
class SelectionResult:
    """Result of a stock selection run."""
    date: str
    top_picks: list[StockScore] = field(default_factory=list)
    all_scored: list[StockScore] = field(default_factory=list)

    @property
    def symbols(self) -> list[str]:
        return [s.symbol for s in self.top_picks]


class StockSelector:
    """Pre-market stock screener — ranks all symbols for the day.

    Usage:
        selector = StockSelector(data_dir="data/lake", top_n=9)
        result = selector.run_for_date("2026-08-17")
        watchlist = result.symbols  # top 9 stocks to monitor
    """

    def __init__(self, data_dir: str, top_n: int = 9,
                 min_turnover: float = 50_000_000,  # ₹5Cr daily min
                 max_gap_pct: float = 0.05,          # skip gaps > 5%
                 min_volume_ratio: float = 1.2,      # at least 1.2x avg volume
                 lookback_days: int = 20):
        self.data_dir = data_dir
        self.top_n = top_n
        self.min_turnover = min_turnover
        self.max_gap_pct = max_gap_pct
        self.min_volume_ratio = min_volume_ratio
        self.lookback_days = lookback_days
        self._cache: dict[str, pd.DataFrame] = {}

    def run_for_date(self, date_str: str) -> SelectionResult:
        """Run selection for a given date. Returns top-N stocks."""
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
        prev_date = date - timedelta(days=1)

        # Find all symbols with data
        symbols = self._find_symbols()
        scored = []

        for symbol in symbols:
            try:
                score = self._score_symbol(symbol, date, prev_date)
                if score is not None:
                    scored.append(score)
            except Exception as e:
                logger.debug(f"Skipping {symbol}: {e}")
                continue

        # Rank by composite score
        scored.sort(key=lambda s: s.score, reverse=True)
        top_picks = scored[:self.top_n]

        result = SelectionResult(date=date_str, top_picks=top_picks, all_scored=scored)
        logger.info(f"Selected {len(top_picks)} stocks for {date_str}: "
                    f"{[s.symbol for s in top_picks]}")
        return result

    def _find_symbols(self) -> list[str]:
        """Find all available equity symbols."""
        import pathlib
        base = pathlib.Path(self.data_dir) / "equities" / "candles" / "timeframe=1m"
        if not base.exists():
            return []
        return [p.name.replace("symbol=", "") for p in base.iterdir() if p.is_dir()]

    def _score_symbol(self, symbol: str, date, prev_date) -> StockScore | None:
        """Score a single symbol for the given date."""
        df = self._load_data(symbol)
        if df is None or len(df) < 30:
            return None

        # Get today's and yesterday's data
        today = df[df["timestamp"].dt.date == date]
        yesterday = df[df["timestamp"].dt.date == prev_date]

        if today.empty or yesterday.empty:
            return None

        prev_close = float(yesterday.iloc[-1]["close"])
        open_price = float(today.iloc[0]["open"])
        price = float(today.iloc[-1]["close"]) if len(today) > 0 else open_price

        if prev_close <= 0:
            return None

        # Gap %
        gap_pct = (open_price - prev_close) / prev_close

        # Skip extreme gaps (likely news-driven, unpredictable)
        if abs(gap_pct) > self.max_gap_pct:
            return None

        # Volume ratio (first 30 min today vs avg 30-min volume)
        today_vol = float(today.head(30)["volume"].sum())
        avg_vol = float(df.tail(self.lookback_days * 30)["volume"].mean())
        volume_ratio = today_vol / avg_vol if avg_vol > 0 else 0

        if volume_ratio < self.min_volume_ratio:
            return None

        # Turnover (liquidity proxy)
        turnover = price * today_vol

        if turnover < self.min_turnover:
            return None

        # EMA alignment (using recent data)
        recent = df.tail(50)
        if len(recent) >= 21:
            ema9 = float(ema(recent, 9).iloc[-1])
            ema21 = float(ema(recent, 21).iloc[-1])
            ema_aligned = ema9 > ema21
        else:
            ema_aligned = False

        # VWAP bias
        if len(today) >= 5:
            vwap_val = float(vwap(today).iloc[-1])
            vwap_bias = (price - vwap_val) / vwap_val if vwap_val > 0 else 0
        else:
            vwap_bias = 0

        # Composite score: gap + volume + EMA + VWAP
        # Higher gap (in trend direction) + higher volume + aligned = better
        score = (abs(gap_pct) * 100 +           # gap magnitude (0-5 points)
                 min(volume_ratio, 5) +          # volume ratio (capped at 5)
                 (1.0 if ema_aligned else 0) +   # EMA alignment bonus
                 max(0, vwap_bias * 10))         # VWAP bias bonus

        return StockScore(
            symbol=symbol, gap_pct=gap_pct, volume_ratio=volume_ratio,
            price=price, prev_close=prev_close, open_price=open_price,
            turnover=turnover, ema_aligned=ema_aligned, vwap_bias=vwap_bias,
            score=score,
        )

    def _load_data(self, symbol: str) -> pd.DataFrame | None:
        """Load recent data for a symbol (cached)."""
        if symbol in self._cache:
            return self._cache[symbol]
        
        import pathlib
        base = (pathlib.Path(self.data_dir) / "equities" / "candles" /
                "timeframe=1m" / f"symbol={symbol}" / "data.parquet")
        if not base.exists():
            return None
        try:
            df = pd.read_parquet(base)
            if df["timestamp"].dtype.kind != "M":
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            # Cache and return
            self._cache[symbol] = df
            return df
        except Exception:
            return None


class SignalScanner:
    """Real-time signal scanner — monitors selected stocks for entries.

    Usage:
        scanner = SignalScanner(watchlist=["RELIANCE", "HDFCBANK"])
        scanner.on_bar(bar_event)  # feed 1m bars
        # scanner.signals contains generated signals
    """

    def __init__(self, watchlist: list[str], strategy: str = "orb_vwap"):
        self.watchlist = set(watchlist)
        self.strategy = strategy
        self.signals: list[dict] = []
        self._bars: dict[str, list[dict]] = {s: [] for s in watchlist}
        self._orb_high: dict[str, float] = {}
        self._orb_low: dict[str, float] = {}
        self._orb_ready: dict[str, bool] = {}
        self._active: dict[str, dict | None] = {s: None for s in watchlist}

    def on_bar(self, symbol: str, bar: dict) -> dict | None:
        """Process a new bar. Returns signal dict if entry/exit triggered."""
        if symbol not in self.watchlist:
            return None

        bars = self._bars[symbol]
        bars.append(bar)

        # Keep only last 100 bars
        if len(bars) > 100:
            bars.pop(0)

        minute = bar.get("minute", 0)

        # Build opening range (09:15-09:30)
        if symbol not in self._orb_ready:
            self._orb_ready[symbol] = False
            self._orb_high[symbol] = 0.0
            self._orb_low[symbol] = float("inf")

        if not self._orb_ready.get(symbol, False):
            # Accumulate range
            if 9 * 60 + 15 <= minute < 9 * 60 + 30:
                self._orb_high[symbol] = max(self._orb_high.get(symbol, 0), bar["high"])
                self._orb_low[symbol] = min(self._orb_low.get(symbol, float("inf")), bar["low"])
            elif minute >= 9 * 60 + 30:
                self._orb_ready[symbol] = True

        # Manage active position
        if self._active.get(symbol) is not None:
            signal = self._manage_exit(symbol, bar)
            if signal:
                self.signals.append(signal)
                return signal

        # Look for entries (09:30-10:30)
        if not self._orb_ready.get(symbol, False):
            return None
        if not (9 * 60 + 30 <= minute < 10 * 60 + 30):
            return None

        signal = self._check_entry(symbol, bar)
        if signal:
            self.signals.append(signal)
        return signal

    def _check_entry(self, symbol: str, bar: dict) -> dict | None:
        """Check for entry signal."""
        bars = self._bars[symbol]
        if len(bars) < 21:
            return None

        close = bar["close"]
        orb_high = self._orb_high.get(symbol, 0)
        orb_low = self._orb_low.get(symbol, 0)

        if orb_high <= orb_low:
            return None

        # VWAP bias
        frame = pd.DataFrame(bars)
        vwap_val = float(vwap(frame).iloc[-1]) if len(frame) > 0 else 0
        ema9 = float(ema(frame, 9).iloc[-1]) if len(frame) >= 9 else 0
        ema21 = float(ema(frame, 21).iloc[-1]) if len(frame) >= 21 else 0

        range_width = orb_high - orb_low
        if range_width <= 0:
            return None

        # Long: close above range + VWAP + EMA alignment
        if close > orb_high and close > vwap_val and ema9 > ema21 > 0:
            sl = max(vwap_val, orb_low)
            tp = close + range_width * 2.0
            self._active[symbol] = {
                "side": "BUY", "entry": close, "sl": sl, "tp": tp,
                "qty": 1, "high": close, "low": close,
            }
            return {"symbol": symbol, "side": "BUY", "price": close, "sl": sl, "tp": tp}

        # Short: close below range + VWAP + EMA alignment
        elif close < orb_low and close < vwap_val and ema9 < ema21 > 0:
            sl = min(vwap_val, orb_high)
            tp = close - range_width * 2.0
            self._active[symbol] = {
                "side": "SELL", "entry": close, "sl": sl, "tp": tp,
                "qty": 1, "high": close, "low": close,
            }
            return {"symbol": symbol, "side": "SELL", "price": close, "sl": sl, "tp": tp}

        return None

    def _manage_exit(self, symbol: str, bar: dict) -> dict | None:
        """Check for exit signal."""
        active = self._active.get(symbol)
        if active is None:
            return None

        high = bar["high"]
        low = bar["low"]
        active["high"] = max(active["high"], high)
        active["low"] = min(active["low"], low)

        if active["side"] == "BUY":
            if low <= active["sl"]:
                pnl = active["sl"] - active["entry"]
                self._active[symbol] = None
                return {"symbol": symbol, "side": "SELL", "price": active["sl"], "reason": "SL", "pnl": pnl, "entry": active["entry"]}
            elif high >= active["tp"]:
                pnl = active["tp"] - active["entry"]
                self._active[symbol] = None
                return {"symbol": symbol, "side": "SELL", "price": active["tp"], "reason": "TP", "pnl": pnl, "entry": active["entry"]}

        elif active["side"] == "SELL":
            if high >= active["sl"]:
                pnl = active["entry"] - active["sl"]
                self._active[symbol] = None
                return {"symbol": symbol, "side": "BUY", "price": active["sl"], "reason": "SL", "pnl": pnl, "entry": active["entry"]}
            elif low <= active["tp"]:
                pnl = active["entry"] - active["tp"]
                self._active[symbol] = None
                return {"symbol": symbol, "side": "BUY", "price": active["tp"], "reason": "TP", "pnl": pnl, "entry": active["entry"]}

        return None
