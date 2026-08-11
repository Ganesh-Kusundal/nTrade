"""nTrade UI backend — FastAPI market-data + live-candle service.

Kept deliberately small: the UI only needs instrument metadata, historical
candles, quotes, and a live candle stream. Everything sits on top of the
existing ``ntrade`` domain/broker layer through the ``MarketDataService``
provider abstraction (see ``api.marketdata``), so the frontend never talks to
a broker API directly.
"""
