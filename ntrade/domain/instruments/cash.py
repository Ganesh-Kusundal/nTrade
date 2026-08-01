"""Cash and spot asset classes."""

from __future__ import annotations

from ntrade.domain.instruments.base import Instrument


class Equity(Instrument):
    KIND = "equity"
    DEFAULT_EXCHANGE = "NSE"

    @property
    def market_cap(self) -> float | None:
        return self._metadata.get("market_cap")


class Index(Instrument):
    KIND = "index"
    DEFAULT_EXCHANGE = "INDEX"


class ETF(Instrument):
    KIND = "etf"
    DEFAULT_EXCHANGE = "NSE"


class Currency(Instrument):
    KIND = "currency"
    DEFAULT_EXCHANGE = "BSE"


class Commodity(Instrument):
    KIND = "commodity"
    DEFAULT_EXCHANGE = "MCX"


class Bond(Instrument):
    KIND = "bond"
    DEFAULT_EXCHANGE = "BSE"


class Crypto(Instrument):
    KIND = "crypto"
    DEFAULT_EXCHANGE = "CRYPTO"


class Spot(Instrument):
    KIND = "spot"
    DEFAULT_EXCHANGE = "NSE"
