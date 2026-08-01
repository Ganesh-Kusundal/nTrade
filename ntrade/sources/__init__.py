"""Sources — interchangeable market event sources (zero parity)."""

from ntrade.sources.market_feed import MarketFeedSource, SimulatedFeedSource
from ntrade.sources.dhan_feed import DhanMarketFeedSource, dhan_payload_to_events
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource

__all__ = [
    "MarketFeedSource", "SimulatedFeedSource", "SyntheticMarketFeedSource",
    "DhanMarketFeedSource", "dhan_payload_to_events",
]
