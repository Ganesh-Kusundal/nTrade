"""Nifty 200 point-in-time scan — volume + relative volume as of HH:MM.

Uses the standard TradingSession / scanner interface from the user-guide:

  session = TradingSession.connect("dhan")
  session.register(session.stock(sym))
  inst.market.history()("5m", days=…)
  session.scanner().volume(…)
  session.scanner().custom(…)

Style A: register_universe(...) then scan
Style B: scan(..., universe="NIFTY200", as_of=...)

Example:
  python examples/nifty200_asof_scan.py --as-of "2026-07-31 09:45" --top 5
  python examples/nifty200_asof_scan.py --as-of "2026-07-31 09:45" --limit 25
"""

from __future__ import annotations

import argparse
import io
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from ntrade import Quote, Scanner, ScannerResult, TradingSession

IST = ZoneInfo("Asia/Kolkata")

# Official NSE Indices constituent CSVs
UNIVERSE_URLS = {
    "NIFTY200": "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "NIFTY50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "NIFTY100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
}


# ---------------------------------------------------------------------------
# Universe helpers (thin wrappers over the standard session API)
# ---------------------------------------------------------------------------

def download_universe(name: str = "NIFTY200") -> list[str]:
    """Download index constituents CSV online; return NSE trading symbols."""
    url = UNIVERSE_URLS[name.upper()]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text))
    col = "Symbol" if "Symbol" in df.columns else df.columns[2]
    return [str(s).strip() for s in df[col].dropna().tolist() if str(s).strip()]


def register_universe(
    session: TradingSession,
    name: str = "NIFTY200",
    *,
    symbols: list[str] | None = None,
    limit: int | None = None,
) -> list:
    """Style A: download (or reuse) symbols and register via session.register()."""
    syms = list(symbols) if symbols is not None else download_universe(name)
    if limit is not None:
        syms = syms[:limit]
    instruments = []
    for sym in syms:
        inst = session.stock(sym)
        session.register(inst)
        instruments.append(inst)
    return instruments


def _parse_as_of(as_of: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(as_of, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"as_of must look like 'YYYY-MM-DD HH:MM', got {as_of!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    if fmt == "%Y-%m-%d":
        dt = dt.replace(hour=9, minute=45)
    return dt


def project_as_of(instruments, as_of: datetime, *, history_days: int = 15, timeframe: str = "5m") -> int:
    """Load history and project each instrument's quote/indicators to ``as_of``.

    Uses the user-guide history API, then applies a Quote so scanners can read
    ltp / volume / avg_volume at that clock time.
    """
    ok = 0
    as_of_ts = pd.Timestamp(as_of)
    for i, inst in enumerate(instruments, 1):
        try:
            series = inst.market.history()(timeframe, days=history_days, force=True)
            df = series.df
            if df is None or df.empty or "timestamp" not in df.columns:
                continue
            ts = pd.to_datetime(df["timestamp"])
            if ts.dt.tz is None:
                ts = ts.dt.tz_localize(IST)
            else:
                ts = ts.dt.tz_convert(IST)
            work = df.copy()
            work["timestamp"] = ts
            work = work[work["timestamp"] <= as_of_ts]
            if work.empty:
                continue

            day = as_of_ts.normalize()
            tod = as_of_ts - day  # timedelta from midnight
            day_bars = work[work["timestamp"].dt.normalize() == day]
            if day_bars.empty:
                continue

            last = day_bars.iloc[-1]
            vol_so_far = int(day_bars["volume"].sum())
            close = float(last["close"])
            high = float(day_bars["high"].max())
            low = float(day_bars["low"].min())
            open_ = float(day_bars.iloc[0]["open"])

            # previous close = last bar before this session day
            prior = work[work["timestamp"].dt.normalize() < day]
            prev_close = float(prior.iloc[-1]["close"]) if not prior.empty else close

            # same-time-of-day average volume over prior sessions (relative volume baseline)
            prior_vols = []
            for d, g in work.groupby(work["timestamp"].dt.normalize()):
                if d >= day:
                    continue
                cut = g[g["timestamp"] <= (d + tod)]
                if not cut.empty:
                    prior_vols.append(float(cut["volume"].sum()))
            avg_vol = sum(prior_vols) / len(prior_vols) if prior_vols else float(vol_so_far)

            inst.apply_quote(Quote(
                ltp=close, open=open_, high=high, low=low,
                prev_close=prev_close, volume=vol_so_far,
                timestamp=as_of.replace(tzinfo=None),
            ))
            inst._indicators["avg_volume"] = avg_vol
            inst._indicators["rvol"] = (vol_so_far / avg_vol) if avg_vol > 0 else 0.0
            ok += 1
        except Exception as exc:
            print(f"  skip {inst.symbol}: {exc}")
        if i % 25 == 0:
            print(f"  projected {i}/{len(instruments)}…")
            time.sleep(0.2)  # be gentle on broker rate limits
    return ok


class AbsoluteVolumeScanner(Scanner):
    """Rank by absolute volume as of the projected quote (user-guide custom scanner)."""

    name = "absolute_volume"
    rate_limit_seconds = 0.0

    def scan(self, session, *, min_volume: int = 1, now=None, **kw):
        results = []
        for inst in session.kernel.ctx.instruments_snapshot():
            vol = int(inst.market.volume() or 0)
            if vol < min_volume:
                continue
            avg = float((inst._indicators or {}).get("avg_volume") or 0)
            rvol = (vol / avg) if avg > 0 else 0.0
            results.append(ScannerResult(
                instrument=inst,
                scanner_name=self.name,
                score=float(vol),
                signal="BUY",
                matched_conditions=("absolute_volume",),
                indicator_values={"volume": vol, "avg_volume": avg, "rvol": round(rvol, 4)},
                timestamp=now or datetime.now(),
            ))
        return results


class RelativeVolumeScanner(Scanner):
    """Rank by volume / typical volume-by-this-time (user-guide custom scanner)."""

    name = "relative_volume"
    rate_limit_seconds = 0.0

    def scan(self, session, *, min_rvol: float = 1.0, now=None, **kw):
        results = []
        for inst in session.kernel.ctx.instruments_snapshot():
            vol = inst.market.volume() or 0
            avg = float((inst._indicators or {}).get("avg_volume") or 0)
            if avg <= 0 or vol <= 0:
                continue
            rvol = vol / avg
            if rvol < min_rvol:
                continue
            results.append(ScannerResult(
                instrument=inst,
                scanner_name=self.name,
                score=round(rvol, 4),
                signal="BUY",
                matched_conditions=("relative_volume",),
                indicator_values={"volume": vol, "avg_volume": avg, "rvol": round(rvol, 4)},
                timestamp=now or datetime.now(),
            ))
        return results


def scan_volume(session, *, as_of=None, universe=None, limit=None, top=5, min_volume=1, **_):
    """Style A/B helper → ``session.scanner().custom(AbsoluteVolumeScanner)``."""
    if universe:
        register_universe(session, universe, limit=limit)
    if as_of is not None:
        instruments = session.kernel.ctx.instruments_snapshot()
        project_as_of(instruments, _parse_as_of(as_of) if isinstance(as_of, str) else as_of)
    return session.scanner().custom(AbsoluteVolumeScanner(), min_volume=min_volume)[:top]


def scan_relative_volume(session, *, as_of=None, universe=None, limit=None, top=5, min_rvol=1.0, **_):
    """Style A/B helper → ``session.scanner().custom(RelativeVolumeScanner)``."""
    if universe:
        register_universe(session, universe, limit=limit)
    if as_of is not None:
        instruments = session.kernel.ctx.instruments_snapshot()
        project_as_of(instruments, _parse_as_of(as_of) if isinstance(as_of, str) else as_of)
    return session.scanner().custom(RelativeVolumeScanner(), min_rvol=min_rvol)[:top]


def _print_hits(title: str, hits) -> None:
    print(f"\n=== {title} ===")
    if not hits:
        print("(no hits)")
        return
    for r in hits:
        iv = r.indicator_values
        print(
            f"  #{r.rank} {r.instrument.symbol:12s}  signal={r.signal:4s}  "
            f"score={r.score:8.2f}  vol={iv.get('volume', 0):>10}  "
            f"avg={iv.get('avg_volume', 0):>10.0f}  "
            f"rvol={iv.get('rvol', r.score):.2f}"
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Nifty 200 as-of volume / RVOL scan")
    p.add_argument("--as-of", default="2026-07-31 09:45", help="YYYY-MM-DD HH:MM IST")
    p.add_argument("--universe", default="NIFTY200", choices=sorted(UNIVERSE_URLS))
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--limit", type=int, default=None, help="cap symbols (smoke test)")
    p.add_argument("--history-days", type=int, default=15)
    args = p.parse_args()

    as_of = _parse_as_of(args.as_of)
    print(f"Connecting Dhan…")
    session = TradingSession.connect("dhan")

    print(f"Downloading {args.universe} CSV…")
    symbols = download_universe(args.universe)
    print(f"  {len(symbols)} constituents")
    if args.limit:
        symbols = symbols[: args.limit]
        print(f"  using first {len(symbols)} (--limit)")

    # --- Style A: register once, then scan ---
    print(f"\n[Style A] register_universe → project {as_of} → scanner")
    instruments = register_universe(session, args.universe, symbols=symbols)
    n = project_as_of(instruments, as_of, history_days=args.history_days)
    print(f"  projected {n}/{len(instruments)} instruments")

    vol_a = session.scanner().custom(AbsoluteVolumeScanner(), min_volume=1)[: args.top]
    rvol_a = session.scanner().custom(RelativeVolumeScanner(), min_rvol=0.0)[: args.top]
    _print_hits(f"Style A — top {args.top} absolute volume @ {as_of}", vol_a)
    _print_hits(f"Style A — top {args.top} relative volume @ {as_of}", rvol_a)

    # --- Style B: one-shot helpers (universe= + as_of=) on already-projected session ---
    print(f"\n[Style B] scan_volume / scan_relative_volume (universe=, as_of=)")
    # Session already has projected universe — Style B is the helper call shape.
    vol_b = scan_volume(session, top=args.top, min_volume=1)
    rvol_b = scan_relative_volume(session, top=args.top, min_rvol=0.0)
    _print_hits(f"Style B — top {args.top} absolute volume @ {as_of}", vol_b)
    _print_hits(f"Style B — top {args.top} relative volume @ {as_of}", rvol_b)

    session.disconnect()


if __name__ == "__main__":
    main()
