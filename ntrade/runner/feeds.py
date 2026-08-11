"""Simple synth/live feed switch — the flag the harness is built around."""

from __future__ import annotations

from ntrade.sim.depth_simulator import DEFAULT_TICK_SIZE


def build_source(kernel, *, feed: str = "synth", symbol: str = "NIFTY",
                 exchange: str = "NSE", frame=None, seed: int = 0,
                 live_kwargs: dict | None = None,
                 depth_levels: int = 0, depth_imbalance: float = 0.0,
                 depth_imbalance_mode: str = "constant",
                 depth_seed: int = 0, depth_tick_size: float = DEFAULT_TICK_SIZE):
    """Return a feed source for the kernel.

    ``synth`` replays a 1m OHLCV ``frame`` as 1s ticks; ``live`` opens the
    Dhan websocket. ``depth_*`` kwargs are forwarded to the synthetic feed
    (opt-in simulated order book so depth-aware strategies like the Valentini
    scalper run offline with their confidence filter active).
    """
    if feed == "synth":
        if frame is None or frame.empty:
            raise ValueError("synth feed requires a non-empty 1m OHLCV 'frame'")
        from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource
        return SyntheticMarketFeedSource(
            kernel, symbol=symbol, exchange=exchange, data=frame, seed=seed,
            depth_levels=depth_levels, depth_imbalance=depth_imbalance,
            depth_imbalance_mode=depth_imbalance_mode,
            depth_seed=depth_seed, depth_tick_size=depth_tick_size)
    if feed == "live":
        from ntrade.sources.dhan_feed import DhanMarketFeedSource
        return DhanMarketFeedSource(kernel, **(live_kwargs or {}))
    raise ValueError(f"unknown feed {feed!r}; expected 'synth' or 'live'")
