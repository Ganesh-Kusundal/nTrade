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
    """All instruments registered in the session's kernel."""
    return list(session.kernel.ctx.instruments.values())


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
            rsi = indicators.get("rsi")
            if rsi is not None and rsi >= rsi_threshold:
                results.append(ScannerResult(
                    instrument=inst, scanner_name=self.name,
                    score=rsi, signal="BUY",
                    matched_conditions=("rsi_momentum",),
                    indicator_values={"rsi": rsi},
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

    Uses ``supertrend`` or ``atr`` indicators if available; otherwise
    compares LTP against a simple high/low range from the quote.
    """

    name = "breakout"

    def scan(self, session: "TradingSession", *, now: datetime | None = None, **kw: Any) -> list[ScannerResult]:
        results: list[ScannerResult] = []
        for inst in _instruments(session):
            ltp = _safe_ltp(inst)
            if not ltp:
                continue
            indicators = _safe_indicators(inst)
            supertrend = indicators.get("supertrend")
            high = inst._quote.high or 0
            low = inst._quote.low or 0
            conditions: list[str] = []
            score = 0.0
            # Supertrend-based breakout
            if supertrend is not None and supertrend > 0:
                if ltp > supertrend:
                    conditions.append("above_supertrend")
                    score = (ltp - supertrend) / supertrend * 100
            # High/low breakout
            elif high > 0 and low > 0:
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
            results.append(ScannerResult(
                instrument=inst, scanner_name=self.name,
                score=round(score, 4), signal=signal,
                matched_conditions=tuple(conditions),
                indicator_values={k: indicators.get(k, 0) for k in ("supertrend", "atr") if indicators.get(k)},
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
