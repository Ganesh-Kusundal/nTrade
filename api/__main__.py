"""``python -m api`` — run the market API server locally.

Provider selection: ``--provider synthetic|dhan|parquet`` (env
``NTRADE_MARKET_PROVIDER`` works too; default ``synthetic`` so the server is
usable offline). The built UI is served from ``ui/dist`` when present.

Live streaming is ON by default and gated by NSE/MCX session hours
(:mod:`api.market_hours`). Pass ``--no-live-stream`` for historical-only.
"""

from __future__ import annotations

import argparse
import os

import uvicorn


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    parser = argparse.ArgumentParser(description="nTrade market API server")
    parser.add_argument(
        "--provider",
        default=os.environ.get("NTRADE_MARKET_PROVIDER", "synthetic"),
        choices=("synthetic", "dhan", "parquet"),
        help="market data provider (default: synthetic, offline-safe)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--live-stream",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("NTRADE_LIVE_STREAM", default=True),
        help="enable live candle pump (default: on; still gated by market hours)",
    )
    args = parser.parse_args()

    from api.server import create_app

    uvicorn.run(
        create_app(args.provider, live_stream=args.live_stream),
        host=args.host, port=args.port,
    )


if __name__ == "__main__":
    main()
