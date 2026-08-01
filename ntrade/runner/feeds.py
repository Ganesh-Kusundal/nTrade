"""Simple synth/live feed switch — the flag the harness is built around."""

from __future__ import annotations


def build_source(kernel, *, feed: str = "synth", symbol: str = "NIFTY",
                 exchange: str = "NSE", frame=None, seed: int = 0,
                 live_kwargs: dict | None = None):
    if feed == "synth":
        if frame is None or frame.empty:
            raise ValueError("synth feed requires a non-empty 1m OHLCV 'frame'")
        from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource
        return SyntheticMarketFeedSource(
            kernel, symbol=symbol, exchange=exchange, data=frame, seed=seed)
    if feed == "live":
        from ntrade.sources.dhan_feed import DhanMarketFeedSource
        return DhanMarketFeedSource(kernel, **(live_kwargs or {}))
    raise ValueError(f"unknown feed {feed!r}; expected 'synth' or 'live'")
