"""Live smoke test — exercises the ntrade framework against the real Dhan API.

Usage:  .venv/bin/python scripts/live_smoke.py
Requires .env with Dhan credentials (shared token store preferred).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.kernel.trading_session import TradingSession  # noqa: E402


def main() -> int:
    print("== ntrade live smoke test ==")
    session = TradingSession.connect("dhan")
    print(f"broker: {session.broker.name}, connected={session.connected}")

    # 1) Quote
    nifty = session.index("NIFTY")
    nifty.refresh()
    print(f"NIFTY quote -> ltp={nifty.ltp} spread={nifty.spread()} mid={nifty.mid_price()}")

    # 2) History
    series = nifty.market.history()("5m", days=1)
    print(f"NIFTY 5m history -> rows={len(series)} cached={series.cached} fresh={series.is_fresh()}")
    print(f"last close={series.df['close'].iloc[-1] if not series.df.empty else 'n/a'}")

    # 3) Indicators over live history
    nifty.compute_indicators()
    print(f"indicators -> rsi={nifty.rsi():.2f} atr={nifty.atr():.2f} vwap={nifty.vwap():.2f}")

    # 4) Option chain (read-only)
    chain = nifty.option_chain(expiry=0, num_strikes=5)
    print(f"chain -> {len(chain)} options, atm_strike={chain.atm_strike}, pcr={chain.pcr()}")
    if chain.atm is not None:
        atm = chain.atm
        print(f"atm -> {atm.symbol} ltp={atm.ltp} greeks.delta={atm.delta} iv={atm.iv}")

    # 5) Balance (read-only)
    print(f"balance -> {session.balance()}")

    # 6) Statistics
    stats = nifty.statistics()
    print(f"statistics -> last={stats.get('last')}, total_return_pct={stats.get('total_return_pct')}%")

    # 7) New endpoints (read-only)
    print(f"live_pnl -> {session.broker.get_live_pnl()}")
    print(f"orderbook -> {len(session.broker.get_orderbook() or [])} entries")
    print(f"tradebook -> {len(session.broker.get_trade_book() or [])} entries")
    try:
        exps = nifty.broker.expiry_list()
        print(f"expiry_list -> {[str(e) for e in (exps or [])]}")
    except AttributeError:
        print("expiry_list -> n/a")
    if chain.atm is not None:
        try:
            lot = chain.atm.broker.lot_size()
            print(f"lot_size (atm) -> {lot}")
        except AttributeError:
            print("lot_size -> n/a")

    print("\nLIVE SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
