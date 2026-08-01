"""Built-in scanner implementations.

Each scanner iterates over instruments registered in the TradingSession's
kernel context and evaluates a specific market condition.  Scanners are
designed to work with whatever data is available — if an instrument has no
quote or indicators, it is silently skipped.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ntrade.domain.scanner import Scanner, ScannerResult

if TYPE_CHECKING:
    from ntrade.kernel.trading_session import TradingSession


def _instruments(session: "TradingSession") -> list:
    """All instruments registered in the session's kernel.

    Uses the lock-guarded snapshot so scanning never races a concurrent
    register() from the feed thread (D-007).
    """
    return session.kernel.ctx.instruments_snapshot()


def _safe_ltp(inst) -> float | None:
    """Best-effort LTP from the instrument's quote."""
    ltp = inst._quote.ltp
    return ltp if ltp and ltp > 0 else None


def _safe_indicators(inst) -> dict:
    """Best-effort indicator bundle from the instrument."""
    return dict(inst._indicators) if hasattr(inst, "_indicators") and inst._indicators else {}


# ================================================================ Gap Scanner

class GapScanner(Scanner):
    """Detect instruments with a gap up or gap down from previous close.

    A gap is measured as the percentage difference between the current open
    (or LTP if open is unavailable) and the previous close.  The scanner
    flags gaps exceeding ``min_gap_pct`` (default 1.0%).
    """

    name = "gap"

    def scan(self, session: "TradingSession", *, min_gap_pct: float = 1.0, now: datetime | None = None, **kw: Any) -> list[ScannerResult]:
        results: list[ScannerResult] = []
        for inst in _instruments(session):
            ltp = _safe_ltp(inst)
            prev = inst._quote.prev_close
            if not ltp or not prev or prev <= 0:
                continue
            gap_pct = (ltp - prev) / prev * 100
            if abs(gap_pct) < min_gap_pct:
                continue
            signal = "BUY" if gap_pct > 0 else "SELL"
            conditions = ("gap_up",) if gap_pct > 0 else ("gap_down",)
            results.append(ScannerResult(
                instrument=inst, scanner_name=self.name,
                score=abs(gap_pct), signal=signal,
                matched_conditions=conditions,
                indicator_values={"gap_pct": round(gap_pct, 4)},
                timestamp=now or datetime.now(),
            ))
        return results


# ============================================================ Volume Spike

class VolumeSpikeScanner(Scanner):
    """Detect instruments with volume significantly above average.

    Compares the current volume to a threshold multiplier of the average
    volume (if available via indicators).  Falls back to absolute
    ``min_volume`` if no average is computed.

    Note: ``avg_volume`` is a per-candle mean over the rolling window, so
    this ratio is meaningful in backtest.  In live mode ``quote.volume`` is
    day-cumulative, which dwarfs the spike ratio — the absolute
    ``min_volume`` fallback is the reliable live signal.
    """

    name = "volume_spike"

    def scan(self, session: "TradingSession", *, min_volume: int = 100_000,
             spike_multiplier: float = 2.0, now: datetime | None = None, **kw: Any) -> list[ScannerResult]:
        results: list[ScannerResult] = []
        for inst in _instruments(session):
            vol = inst._quote.volume or 0
            indicators = _safe_indicators(inst)
            avg_vol = indicators.get("avg_volume", 0)
            if avg_vol and avg_vol > 0:
                ratio = vol / avg_vol
                if ratio < spike_multiplier:
                    continue
                score = ratio
            else:
                if vol < min_volume:
                    continue
                score = vol / max(min_volume, 1)
            results.append(ScannerResult(
                instrument=inst, scanner_name=self.name,
                score=round(score, 4), signal="BUY",
                matched_conditions=("volume_spi",),
                indicator_values={"volume": vol, "avg_volume": avg_vol},
                timestamp=now or datetime.now(),
            ))
        return results


# ============================================================== Momentum

class MomentumScanner(Scanner):
    """Detect instruments with strong momentum via RSI or price change.

    Uses the RSI indicator if available; otherwise falls back to the
    percentage change from ``prev_close`` to LTP.
    """

    name = "momentum"

    def scan(self, session: "TradingSession", *, rsi_threshold: float = 60.0,
             min_change_pct: float = 1.0, now: datetime | None = None, **kw: Any) -> list[ScannerResult]:
        results: list[ScannerResult] = []
        for inst in _instruments(session):
            ltp = _safe_ltp(inst)
            if not ltp:
                continue
            indicators = _safe_indicators(inst)
            rsi = indicators.get("rsi_14")
            if rsi is not None and rsi >= rsi_threshold:
                results.append(ScannerResult(
                    instrument=inst, scanner_name=self.name,
                    score=rsi, signal="BUY",
                    matched_conditions=("rsi_momentum",),
                    indicator_values={"rsi_14": rsi},
                    timestamp=now or datetime.now(),
                ))
                continue
            prev = inst._quote.prev_close
            if prev and prev > 0:
                change = (ltp - prev) / prev * 100
                if abs(change) >= min_change_pct:
                    signal = "BUY" if change > 0 else "SELL"
                    results.append(ScannerResult(
                        instrument=inst, scanner_name=self.name,
                        score=abs(change), signal=signal,
                        matched_conditions=("price_momentum",),
                        indicator_values={"change_pct": round(change, 4)},
                        timestamp=now or datetime.now(),
                    ))
        return results


# ============================================================== Breakout

class BreakoutScanner(Scanner):
    """Detect instruments breaking above recent highs or below recent lows.

    Uses a numeric ``stx_10_3`` (supertrend) level if available; otherwise
    compares LTP against a simple high/low range from the quote.  Because the
    indicator pipeline emits ``stx_10_3`` as the supertrend direction string
    ("up"/"down"), the numeric branch is dormant today and the high/low range
    is the working path.
    """

    name = "breakout"

    def scan(self, session: "TradingSession", *, now: datetime | None = None, **kw: Any) -> list[ScannerResult]:
        results: list[ScannerResult] = []
        for inst in _instruments(session):
            ltp = _safe_ltp(inst)
            if not ltp:
                continue
            indicators = _safe_indicators(inst)
            stx = indicators.get("stx_10_3")
            high = inst._quote.high or 0
            low = inst._quote.low or 0
            conditions: list[str] = []
            score = 0.0
            # Supertrend-based breakout — stx_10_3 is only a price band when it
            # is actually numeric; the pipeline emits the direction string
            # ("up"/"down"), so a non-numeric stx falls through to the
            # high/low path below instead of raising a type error.
            if isinstance(stx, (int, float)) and stx > 0:
                if ltp > stx:
                    conditions.append("above_supertrend")
                    score = (ltp - stx) / stx * 100
            # High/low breakout
            if not conditions and high > 0 and low > 0:
                range_ = high - low
                if range_ > 0:
                    if ltp >= high:
                        conditions.append("high_breakout")
                        score = (ltp - high) / range_ * 100
                    elif ltp <= low:
                        conditions.append("low_breakout")
                        score = (low - ltp) / range_ * 100
            if not conditions:
                continue
            signal = "BUY" if "low_breakout" not in conditions else "SELL"
            # indicator_values is dict[str, float] — only include numeric
            # indicators (stx_10_3 is a direction string from the pipeline).
            indicator_values: dict[str, float] = {}
            for k in ("stx_10_3", "atr_14"):
                v = indicators.get(k)
                if isinstance(v, (int, float)):
                    indicator_values[k] = v
            results.append(ScannerResult(
                instrument=inst, scanner_name=self.name,
                score=round(score, 4), signal=signal,
                matched_conditions=tuple(conditions),
                indicator_values=indicator_values,
                timestamp=now or datetime.now(),
            ))
        return results


# ============================================================== Imbalance

class ImbalanceScanner(Scanner):
    """Detect order-book imbalance (bid vs ask pressure).

    Requires depth data on the instrument.  Computes the bid-ask ratio
    and flags significant imbalances.
    """

    name = "imbalance"

    def scan(self, session: "TradingSession", *, imbalance_ratio: float = 2.0, now: datetime | None = None, **kw: Any) -> list[ScannerResult]:
        results: list[ScannerResult] = []
        for inst in _instruments(session):
            depth = inst._depth
            if depth is None or not depth.bids or not depth.asks:
                continue
            bid_qty = sum(l.quantity for l in depth.bids)
            ask_qty = sum(l.quantity for l in depth.asks)
            if ask_qty == 0:
                continue
            ratio = bid_qty / ask_qty
            if ratio >= imbalance_ratio:
                results.append(ScannerResult(
                    instrument=inst, scanner_name=self.name,
                    score=round(ratio, 4), signal="BUY",
                    matched_conditions=("bid_heavy",),
                    indicator_values={"bid_qty": bid_qty, "ask_qty": ask_qty, "ratio": round(ratio, 4)},
                    timestamp=now or datetime.now(),
                ))
            elif ratio > 0 and (1 / ratio) >= imbalance_ratio:
                results.append(ScannerResult(
                    instrument=inst, scanner_name=self.name,
                    score=round(1 / ratio, 4), signal="SELL",
                    matched_conditions=("ask_heavy",),
                    indicator_values={"bid_qty": bid_qty, "ask_qty": ask_qty, "ratio": round(ratio, 4)},
                    timestamp=now or datetime.now(),
                ))
        return results
