"""Live read-only check — verifies the Dhan connection and every read/get
endpoint exposed by the ntrade framework against the real API.

Usage:  .venv/bin/python scripts/live_read_check.py
Requires .env with Dhan credentials (shared token store preferred).
No orders are placed; every call is read-only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.kernel.trading_session import TradingSession  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def exit_code(results, strict: bool = False) -> int:
    """Exit decision for a live-read run (T-034).

    FAIL rows always fail the run (a crashed endpoint is never go-live safe).
    DEGRADED rows (degenerate but non-crashing reads, e.g. lot_size 0) fail
    closed under ``strict`` — for go-live, a DEGRADED market-data row means a
    strategy may size wrong. Plain ``strict=False`` (diagnostics) tolerates
    DEGRADED.
    """
    failed = [name for name, status, _ in results if status == "FAIL"]
    degraded = [name for name, status, _ in results if status == "DEGRADED"]
    if failed:
        return 1
    if strict and degraded:
        return 1
    return 0


def check(name: str, fn, sane=None):
    """Run one read endpoint; record PASS / FAIL / DEGRADED.

    `sane` is an optional predicate on the return value: when it exists and the
    value fails it, the row is marked DEGRADED (a swallowed error producing a
    degenerate default, e.g. lot_size 0) instead of a silent PASS.
    """
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))
        return
    status = "PASS"
    summary = str(value)
    if sane is not None and not sane(value):
        status = "DEGRADED"
        summary = f"{summary} (sanity: expected truthy/non-degenerate)"
    if len(summary) > 110:
        summary = summary[:107] + "..."
    RESULTS.append((name, status, summary))


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Live read-only check (T-034 strict mode)")
    parser.add_argument("--strict", action="store_true",
                        help="fail closed on DEGRADED rows (recommended for go-live)")
    args = parser.parse_args(argv)

    print("== ntrade live read-only check ==")

    # ------------------------------------------------------------ connection
    global _tsl
    try:
        session = TradingSession.connect("dhan")
        ok = session.connected and session.broker.tsl is not None
        RESULTS.append(("connect", "PASS" if ok else "FAIL",
                        f"broker={session.broker.name} connected={session.connected}"))
        _tsl = session.broker.tsl
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(("connect", "FAIL", f"{type(exc).__name__}: {exc}"))
        print("connection failed; aborting endpoint checks")
        _report()
        return 1

    nifty = session.index("NIFTY")
    rel = session.index("RELIANCE")

    # ------------------------------------------------------------ market data
    check("quote.ltp (NIFTY)", lambda: nifty.refresh() and nifty.market.ltp(), sane=lambda v: v > 0)
    check("quote.full (get_quote_data)", lambda: nifty.market.quote().as_dict(),
          sane=lambda d: isinstance(d, dict) and d.get("ltp", 0) > 0)
    check("quote.ohlc (get_ohlc_data)", lambda: nifty.broker.ohlc(),
          sane=lambda d: isinstance(d, dict) and bool(d))
    check("depth (NIFTY index -> None expected)", lambda: session.broker.get_depth(nifty))
    # NSE equity: the ONLY path where get_depth is genuinely exercised live.
    check("depth (RELIANCE NSE)", lambda: session.broker.get_depth(rel),
          sane=lambda d: d is not None and len(d.bids) > 0)
    check("depth20 capability (RELIANCE)", lambda: rel.broker.depth20(levels=5),
          sane=lambda d: len(d.bids) > 0)

    check("history 5m (NIFTY)", lambda: len(nifty.market.history()("5m", days=1).df), sane=lambda v: v > 0)
    check("history DAY (NIFTY)", lambda: len(nifty.market.history()("1d", days=5).df), sane=lambda v: v > 0)
    check("history long-term (1d, 2026-07-01..2026-07-31)",
          lambda: len(session.broker.get_long_term_historical(nifty, timeframe="1d",
                                                       from_date="2026-07-01", to_date="2026-07-31")),
          sane=lambda v: v > 0)

    check("expiry_list (NIFTY)", lambda: session.broker.get_expiry_list(nifty)[:3],
          sane=lambda v: len(v) > 0)
    check("expiry_date (NIFTY OPTION)", lambda: session.broker.get_expiry_date(nifty, "OPTION"),
          sane=lambda v: len(v) > 0)
    check("future_script (NIFTY expiry=0)", lambda: session.broker.get_future_script(nifty, 0),
          sane=lambda v: bool(v))
    check("start_date", lambda: session.broker.get_start_date(), sane=lambda v: bool(v))
    check("instrument_file", lambda: str(session.broker.get_instrument_file()),
          sane=lambda v: bool(v) and v != "None")

    # ------------------------------------------------------------ option chain
    chain_ref = {}

    def fetch_chain():
        chain = nifty.derivatives.option_chain(expiry=0, num_strikes=5)
        chain_ref["chain"] = chain
        return f"options={len(chain)} atm={chain.atm_strike} expiry={chain.target_expiry} idx_used={chain.expiry_index_used}"

    check("option_chain (NIFTY)", fetch_chain, sane=lambda _: chain_ref.get("chain") is not None)

    if chain_ref.get("chain") is not None:
        chain = chain_ref["chain"]
        check("chain.atm + greeks", lambda: f"{chain.atm.symbol} delta={chain.atm.delta:.4f} iv={chain.atm.iv:.4f}",
              sane=lambda _: chain.atm is not None)
        check("chain.calls / chain.puts", lambda: f"calls={len(chain.calls)} puts={len(chain.puts)}",
              sane=lambda _: len(chain) > 0)
        check("chain.pcr", lambda: chain.pcr())
        check("chain.max_pain", lambda: chain.max_pain(), sane=lambda v: v > 0)
        # NOTE: Dhan's ATM/OTM strike selection internally calls LTP which is
        # flaky (seen returning None tuples); ITM is usually fine. DEGRADED is
        # reported rather than PASS when the library returns degenerate tuples.
        check("atm_strike selection", lambda: nifty.broker.atm_strike(expiry=0),
              sane=lambda v: v[0] is not None)
        check("itm_strike selection", lambda: nifty.broker.itm_strike(expiry=0, count=1),
              sane=lambda v: v[0] is not None)
        check("otm_strike selection", lambda: nifty.broker.otm_strike(expiry=0, count=1),
              sane=lambda v: v[0] is not None)
        check("lot_size (atm option)", lambda: session.broker.get_lot_size(chain.atm),
              sane=lambda v: v > 0)
        check("dhan_symbol (atm option)", lambda: _dhan_symbol_of(chain.atm),
              sane=lambda s: s.endswith("CALL") or s.endswith("PUT"))

    # ------------------------------------------------------------ expired data (best effort)
    check("expired_option_data (best effort)", _best_effort_expired_data)

    # ------------------------------------------------------------ account
    check("balance", lambda: session.balance())
    check("live_pnl", lambda: session.broker.get_live_pnl())
    check("positions", lambda: session.positions())
    check("holdings (account)", lambda: len(session.account().holdings))
    check("orderbook", lambda: len(session.broker.get_orderbook()))
    check("tradebook", lambda: len(session.broker.get_trade_book()))
    check("order_report", lambda: sorted(session.broker.order_report().keys()))

    _report()
    failed = [name for name, status, _ in RESULTS if status == "FAIL"]
    degraded = [name for name, status, _ in RESULTS if status == "DEGRADED"]
    print(f"\nRESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} endpoints OK"
          f" ({len(degraded)} degraded: {degraded})")
    return exit_code(RESULTS, strict=args.strict)


def _dhan_symbol_of(instrument) -> str:
    from ntrade.brokers.dhan import dhan_symbol
    return dhan_symbol(instrument)


def _best_effort_expired_data():
    """Look up one expired NIFTY option in the instrument file and pull its
    expired OHLC. Returns a human summary; exceptions are caught by check()."""
    tsl = _tsl
    idf = tsl.instrument_df
    expired = idf[(idf["SEM_EXPIRY_FLAG"].astype(str).str.contains("EXPIRED", na=False)
                   | (idf["SEM_EXPIRY_FLAG"].astype(str) == "True"))
                  & idf["SEM_TRADING_SYMBOL"].astype(str).str.match(r"^NIFTY-.*-CE$", na=False)]
    if expired.empty:
        return "no expired NIFTY CE contract in file (skipped)"
    row = expired.iloc[0]
    symbol = row["SEM_TRADING_SYMBOL"]
    raw_code = row.get("SEM_EXPIRY_CODE", 0)
    import pandas as pd
    expiry_code = int(raw_code) if raw_code is not None and not (isinstance(raw_code, float) and pd.isna(raw_code)) else 0
    df = tsl.get_expired_option_data(
        tradingsymbol=symbol, exchange="NSE", interval=5,
        expiry_flag=str(row.get("SEM_EXPIRY_FLAG", "")),
        expiry_code=expiry_code,
        strike="ATM", option_type="CALL",
        from_date="2026-06-01", to_date="2026-07-31",
    )
    rows = len(df) if df is not None else 0
    return f"{symbol} -> {rows} rows"


def _report() -> None:
    print("\n" + "=" * 78)
    print(f"{'ENDPOINT':<42} {'STATUS':<9} VALUE")
    print("=" * 78)
    for name, status, value in RESULTS:
        print(f"{name:<42} {status:<9} {value}")
    print("=" * 78)


_tsl = None  # set by main() after connection


if __name__ == "__main__":
    sys.exit(main())
