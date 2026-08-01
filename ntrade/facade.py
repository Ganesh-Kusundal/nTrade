"""Market — the legacy public facade (prefer ``TradingSession``).

A thin adapter over ``TradingSession``: every instrument/account/lifecycle
call delegates to the unified session, so there is exactly one implementation
of the SDK API (T-004 consolidation). Kept for backward compatibility.

Simplest entry point (legacy):::

    m = Market(broker="dhan")          # live Dhan
    nifty = m.equity("NIFTY")
    nifty.market.refresh()

Preferred entry point::

    session = TradingSession.connect("dhan")
    nifty = session.stock("NIFTY")
    nifty.market.refresh()
"""

from __future__ import annotations

from ntrade.factories import InstrumentFactory
from ntrade.kernel.trading_session import TradingSession
from ntrade.registry import BrokerRegistry


class Market:
    def __init__(self, broker: str | None = None, broker_instance=None, env_path: str = ".env", env: dict | None = None):
        if broker_instance is not None:
            self.broker = broker_instance
        elif broker is not None:
            self.broker = BrokerRegistry.get(broker, env_path=env_path, env=env)
        else:
            self.broker = BrokerRegistry.get("paper")
        self._session = TradingSession(broker=self.broker, mode="live")
        self.instruments: InstrumentFactory = self._session.factory

    # ------------------------------------------------------------------ access
    def equity(self, symbol, exchange=None, **kw):
        return self._session.stock(symbol, exchange, **kw)

    def index(self, symbol, exchange=None, **kw):
        return self._session.index(symbol, exchange, **kw)

    def etf(self, symbol, exchange=None, **kw):
        return self._session.etf(symbol, exchange, **kw)

    def commodity(self, symbol, exchange=None, **kw):
        return self._session.commodity(symbol, exchange, **kw)

    def currency(self, symbol, exchange=None, **kw):
        return self._session.currency(symbol, exchange, **kw)

    def option(self, underlying, strike, expiry, option_type, **kw):
        return self._session.option(underlying, strike, expiry, option_type, **kw)

    def chain(self, underlying, expiry=0, num_strikes=10, **kw):
        # OptionChain fetch lives on the domain object itself.
        return self._session.chain(underlying, expiry=expiry, num_strikes=num_strikes, **kw)

    # ------------------------------------------------------------------ account
    def balance(self) -> float:
        return self._session.balance()

    def positions(self):
        return self._session.positions()

    def live_pnl(self) -> float:
        return self._session.live_pnl()

    def orderbook(self):
        return self._session.orderbook()

    def tradebook(self):
        return self._session.tradebook()

    def order_report(self):
        return self._session.order_report()

    def account(self):
        """Account composite (balance + holdings)."""
        return self._session.account()

    def portfolio(self):
        """Portfolio composite (positions + holdings)."""
        return self._session.portfolio()

    def connect(self):
        self._session.connect_broker()
        return self

    def disconnect(self):
        self._session.disconnect()

    @property
    def connected(self) -> bool:
        return self._session.connected

    def __repr__(self) -> str:
        return f"Market(broker={self.broker.name!r}, connected={self.broker.connected})"
