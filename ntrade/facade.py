"""Market — the legacy public facade (prefer ``TradingSession``).

Simplest entry point (legacy)::
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
from ntrade.registry import BrokerRegistry


class Market:
    def __init__(self, broker: str | None = None, broker_instance=None, env_path: str = ".env", env: dict | None = None):
        if broker_instance is not None:
            self.broker = broker_instance
        elif broker is not None:
            self.broker = BrokerRegistry.get(broker, env_path=env_path, env=env)
        else:
            self.broker = BrokerRegistry.get("paper")
        self.instruments = InstrumentFactory(self.broker)

    # ------------------------------------------------------------------ access
    def equity(self, symbol, exchange=None, **kw):
        return self.instruments.equity(symbol, exchange, **kw)

    def index(self, symbol, exchange=None, **kw):
        return self.instruments.index(symbol, exchange, **kw)

    def etf(self, symbol, exchange=None, **kw):
        return self.instruments.etf(symbol, exchange, **kw)

    def commodity(self, symbol, exchange=None, **kw):
        return self.instruments.commodity(symbol, exchange, **kw)

    def currency(self, symbol, exchange=None, **kw):
        return self.instruments.currency(symbol, exchange, **kw)

    def option(self, underlying, strike, expiry, option_type, **kw):
        return self.instruments.option(underlying, strike, expiry, option_type, **kw)

    def chain(self, underlying, expiry=0, num_strikes=10, **kw):
        # OptionChain fetch lives on the domain object itself.
        return underlying.derivatives.option_chain(expiry=expiry, num_strikes=num_strikes, **kw)

    # ------------------------------------------------------------------ account
    def balance(self) -> float:
        return self.broker.get_balance()

    def positions(self):
        return self.broker.get_positions()

    def live_pnl(self) -> float:
        return self.broker.get_live_pnl()

    def orderbook(self):
        return self.broker.get_orderbook()

    def tradebook(self):
        return self.broker.get_trade_book()

    def order_report(self):
        return self.broker.order_report()

    def account(self):
        """Account composite (balance + holdings)."""
        from ntrade.domain.portfolio import Account
        return Account.from_broker(self.broker)

    def portfolio(self):
        """Portfolio composite (positions + holdings)."""
        from ntrade.domain.portfolio import Portfolio
        return Portfolio.from_broker(self.broker)

    def connect(self):
        self.broker.connect()
        return self

    def disconnect(self):
        self.broker.disconnect()

    @property
    def connected(self) -> bool:
        return self.broker.connected

    def __repr__(self) -> str:
        return f"Market(broker={self.broker.name!r}, connected={self.broker.connected})"
