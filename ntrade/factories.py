"""Factories — object creation for instruments, options and orders.

Instruments are created via SymbolMaster so repeated lookups share instances.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from ntrade.domain.instruments.base import Instrument
from ntrade.domain.instruments.cash import Commodity, Currency, Equity, ETF, Index, Spot
from ntrade.domain.instruments.derivatives import Future, Option, SyntheticInstrument
from ntrade.registry import SymbolMaster

if TYPE_CHECKING:
    from ntrade.domain.ports import BrokerAdapter


class InstrumentFactory:
    def __init__(self, broker: "BrokerAdapter | None" = None, master: SymbolMaster | None = None):
        self.broker = broker
        self.master = master or SymbolMaster()

    def _broker_kwargs(self) -> dict:
        return {"broker": self.broker} if self.broker is not None else {}

    def equity(self, symbol: str, exchange: str | None = None, **kw) -> Equity:
        return self.master.get(Equity, symbol, exchange, **_broker_kw(self.broker), **kw)

    def index(self, symbol: str, exchange: str | None = None, **kw) -> Index:
        return self.master.get(Index, symbol, exchange, **_broker_kw(self.broker), **kw)

    def etf(self, symbol: str, exchange: str | None = None, **kw) -> ETF:
        return self.master.get(ETF, symbol, exchange, **_broker_kw(self.broker), **kw)

    def commodity(self, symbol: str, exchange: str | None = None, **kw) -> Commodity:
        return self.master.get(Commodity, symbol, exchange, **_broker_kw(self.broker), **kw)

    def currency(self, symbol: str, exchange: str | None = None, **kw) -> Currency:
        return self.master.get(Currency, symbol, exchange, **_broker_kw(self.broker), **kw)

    def spot(self, symbol: str, exchange: str | None = None, **kw) -> Spot:
        return self.master.get(Spot, symbol, exchange, **_broker_kw(self.broker), **kw)

    def option(self, underlying: Instrument, strike: float, expiry: date, option_type: str, **kw) -> Option:
        symbol = kw.pop("symbol", None) or f"{underlying.symbol} {int(strike)} {option_type} {expiry:%d%b%y}"
        opt = Option(
            symbol, exchange="NFO", strike=strike, expiry=expiry,
            option_type=option_type, underlying_symbol=underlying.symbol,
            **_broker_kw(self.broker), **kw,
        )
        opt.set_underlying(underlying)
        return opt

    def future(self, underlying: Instrument, expiry: date, **kw) -> Future:
        symbol = kw.pop("symbol", None) or f"{underlying.symbol} {expiry:%d%b%y}"
        fut = Future(
            symbol, exchange="NFO", underlying=underlying.symbol, expiry=expiry,
            **_broker_kw(self.broker), **kw,
        )
        fut.set_underlying(underlying)
        return fut

    def synthetic(self, symbol: str, legs: list[Instrument], **kw) -> SyntheticInstrument:
        return SyntheticInstrument(symbol, legs, **_broker_kw(self.broker), **kw)



def _broker_kw(broker):
    return {"broker": broker} if broker is not None else {}


class OptionFactory:
    """Synthetic options for analytics/testing (no broker needed)."""

    @staticmethod
    def create(underlying: Instrument, strike: float, expiry: date, option_type: str, **kw) -> Option:
        symbol = kw.pop("symbol", None) or f"{underlying.symbol} {int(strike)} {option_type} {expiry:%d%b%y}"
        opt = Option(symbol, exchange="NFO", strike=strike, expiry=expiry,
                     option_type=option_type, underlying_symbol=underlying.symbol, **kw)
        opt.set_underlying(underlying)
        return opt
