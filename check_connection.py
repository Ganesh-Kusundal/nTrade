"""Connection check for Dhan Tradehull.

Thin CLI over ntrade.brokers.dhan_auth — all auth (shared token store, JWT
expiry, PIN+TOTP fallback, cooldown) lives there, so nothing is duplicated here.
Only logs in and reads market data — places no orders.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ntrade.brokers.dhan_auth import get_tradehull  # noqa: E402


def main() -> int:
    tsl = get_tradehull(env_path=str(ROOT / ".env"))

    print("[1/3] Fetching live market data (NIFTY LTP) ...")
    try:
        data = tsl.get_ltp_data(names=["NIFTY"])
        nifty_ltp = data.get("NIFTY")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: LTP fetch failed: {exc}")
        return 1
    if nifty_ltp is None:
        print("ERROR: LTP fetch returned no data — connection may be broken.")
        return 1
    print(f"      NIFTY LTP = {nifty_ltp}")

    print("[2/3] Fetching account balance ...")
    # NOTE: the library returns 0 silently on API errors, so a 0 here may
    # mean "could not fetch" rather than an empty account.
    print(f"      Available balance = {tsl.get_balance()}")

    print("\nCONNECTION CHECK PASSED — Dhan Tradehull is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
