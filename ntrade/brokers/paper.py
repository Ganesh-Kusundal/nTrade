"""PaperBroker — an in-memory broker used by tests, backtests and replays.

Implements the same BrokerAdapter contract as live brokers, so domain code is
identical across paper and live trading (consistent APIs principle).
"""

from __future__ import annotations

import random
from datetime import timedelta

import pandas as pd

from ntrade.domain.constants import DEFAULT_INITIAL_CASH, OptionType
from ntrade.domain.ports import BrokerAdapter
from ntrade.domain.market.candles import CandleSeries
from ntrade.domain.portfolio import Position
from ntrade.domain.market.quote import Quote, Tick
from ntrade.domain.orders.book import OrderBook, OrderBookEntry, TradeBook, TradeBookEntry
from ntrade.domain.orders.order import Order, OrderStatus, OrderType


class PaperBroker(BrokerAdapter):
    name = "paper"
    # The paper balance is the seeded opening cash, not real money —
    # PositionSyncEngine must not reconcile kernel cash from it (single
    # ledger: PortfolioEngine owns cash, charged with costs exactly once).
    reports_cash = False

    def __init__(self, seed: int = 42, clock=None, **kwargs):
        # Accept (and ignore) broker-generic kwargs like env_path/env so the
        # BrokerRegistry can construct any broker uniformly. ``clock`` lets
        # replay/backtest pin paper timestamps to the kernel clock.
        super().__init__(clock=clock)
        self._random = random.Random(seed)
        self._quotes: dict[str, Quote] = {}
        self._history: dict[str, pd.DataFrame] = {}
        self._orders: list[Order] = []
        self._balance = DEFAULT_INITIAL_CASH
        self._positions: dict[str, Position] = {}
        self._connected = True  # always available

    # ------------------------------------------------------------- seeding
    def seed_quote(self, symbol: str, ltp: float, **kw) -> Quote:
        q = Quote(ltp=ltp, bid=ltp - 0.05, ask=ltp + 0.05, prev_close=ltp, timestamp=self._ts(), **kw)
        self._quotes[symbol.strip().upper()] = q
        return q

    def seed_history(self, symbol: str, rows: int = 200, timeframe: str = "5m",
                     start_price: float = 100.0) -> pd.DataFrame:
        """Synthetic history for tests/replays — explicitly seeded only. Never
        auto-generated on a read path (data reads must stay honest)."""
        end = self._ts()
        start = end - timedelta(minutes=5 * rows)
        ts = [start + timedelta(minutes=5 * i) for i in range(rows)]
        close = [start_price]
        for _ in range(rows - 1):
            close.append(close[-1] * (1 + self._random.uniform(-0.01, 0.01)))
        df = pd.DataFrame({
            "timestamp": ts,
            "open": close, "high": [c * 1.005 for c in close],
            "low": [c * 0.995 for c in close], "close": close,
            "volume": [self._random.randint(100, 5000) for _ in range(rows)],
        })
        self._history[f"{symbol}:{timeframe}"] = df
        return df

    # ------------------------------------------------------------- market data
    def connect(self):
        self._connected = True
        return self

    def get_quote(self, instrument) -> Quote:
        """Real quote only — the feed's seeded/quote-read price. Raises instead
        of fabricating a 100.0 placeholder: paper has no market price until a
        real one exists (see ``place_order`` — never fill a phantom quote)."""
        symbol = instrument if isinstance(instrument, str) else instrument.symbol
        q = self._quotes.get(symbol.strip().upper())
        if q is None:
            raise ValueError(
                f"no live quote for {symbol} (paper requires a real feed)")
        return q

    def get_historical(self, instrument, timeframe="5m", days=None, start=None, end=None) -> CandleSeries:
        key = f"{instrument.symbol}:{timeframe}"
        if key not in self._history:
            raise ValueError(
                f"no seeded history for {instrument.symbol} ({timeframe}); "
                f"paper requires an explicit seed_history (synthetic data reads)")
        df = self._history[key]
        if start is not None:
            df = df[df["timestamp"] >= start]
        if end is not None:
            df = df[df["timestamp"] <= end]
        return CandleSeries(df.reset_index(drop=True), symbol=instrument.symbol, timeframe=timeframe)

    def get_option_chain(self, underlying, expiry=0, num_strikes=10, **kwargs):
        from ntrade.domain.instruments.chain import OptionChain
        from ntrade.domain.instruments.derivatives import Option
        spot = underlying._quote.ltp or 100.0
        atm = round(spot / 50) * 50
        strikes = [atm + (i - num_strikes // 2) * 50 for i in range(num_strikes)]
        options = []
        for s in strikes:
            for otype in (OptionType.CE, OptionType.PE):
                opt = Option(
                    symbol=f"{underlying.symbol} {s} {otype.value}", exchange="NFO",
                    strike=s, expiry=self._ts().date(), option_type=otype,
                    underlying_symbol=underlying.symbol, broker=self,
                )
                opt._quote = opt._quote.with_update(ltp=5.0, oi=self._random.randint(1000, 50000))
                options.append(opt)
        return OptionChain(underlying, options, atm_strike=atm)

    # ------------------------------------------------------------- orders
    def place_order(self, order: Order) -> Order:
        # Prefer the instrument's live quote (kept current by the kernel from
        # ticks/replays) so paper fills track the market; fall back to the
        # seeded quote dict for static tests where no live quote exists. A
        # bar-close reference price (> 0) wins over the live LTP — zero-parity
        # with backtest/replay, which fill at the bar close that generated the
        # signal rather than the (possibly contaminated) next-bar LTP.
        live = getattr(order.instrument, "_quote", None)
        live_ltp = getattr(live, "ltp", 0.0) or 0.0
        seated = self._quotes.get(order.instrument.symbol.strip().upper())
        # TODO: use execution.order_types.strategy_for when adding new types
        if order.order_type.value != "MARKET" and order.price:
            fill_price = order.price
        elif order.reference_price > 0.0:
            fill_price = order.reference_price  # bar-close reference (zero-parity)
        elif live_ltp > 0.0:
            fill_price = live_ltp
        elif seated is not None:
            fill_price = seated.ltp  # explicitly seeded quote (real price)
        else:
            # Never fill with no price: get_quote now raises, so a MARKET
            # fill with no live/reference/seeded price is a phantom fill.
            raise ValueError(
                f"no market price available for paper MARKET order on "
                f"{order.instrument.symbol}")
        order.order_id = f"PAPER-{len(self._orders) + 1}"
        order.status = OrderStatus.COMPLETED
        order.filled_qty = order.quantity
        order.avg_price = round(fill_price, 2)
        self._orders.append(order)
        # ponytail: single ledger — the kernel's PortfolioEngine owns cash
        # (notional + commission + statutory, charged exactly once). The
        # broker book keeps positions/fills only; _balance stays at the
        # seeded opening cash so get_balance() reports session start.
        fill_price = order.avg_price
        pos = self._positions.get(order.instrument.symbol)
        delta = order.quantity if order.side.value == "BUY" else -order.quantity
        qty = (pos.quantity if pos else 0) + delta
        if qty == 0:
            # Flattened: drop the position (mirror of PortfolioEngine).
            self._positions.pop(order.instrument.symbol, None)
        elif pos is None:
            self._positions[order.instrument.symbol] = Position(
                symbol=order.instrument.symbol, quantity=qty,
                avg_price=fill_price, ltp=fill_price,
                product=order.trade_type.value)
        else:
            # Same-direction add -> weighted average; partial exit (same sign)
            # keeps the entry price; exit-and-reverse -> fresh at the fill.
            if pos.quantity * qty > 0 and abs(qty) > abs(pos.quantity):
                total = pos.avg_price * abs(pos.quantity) + fill_price * order.quantity
                pos.avg_price = round(total / abs(qty), 4)
            elif pos.quantity * qty < 0:
                pos.avg_price = fill_price  # reversed: residual opens fresh
            pos.quantity = qty
            pos.ltp = fill_price
        return order

    # ------------------------------------------------- order lifecycle
    def cancel_order(self, order: Order) -> Order:
        if order.order_id is not None:
            for stored in self._orders:
                if stored.order_id == order.order_id and stored.status in (OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED):
                    stored.status = OrderStatus.CANCELLED
                    order.status = OrderStatus.CANCELLED
                    return order
        order.status = OrderStatus.CANCELLED
        return order

    def modify_order(self, order: Order, *, price=None, quantity=None, order_type=None, trigger_price=None) -> Order:
        if price is not None:
            order.price = price
        if quantity is not None:
            order.quantity = quantity
        if trigger_price is not None:
            order.trigger_price = trigger_price
        if order_type is not None:
            order.order_type = order_type if isinstance(order_type, OrderType) else OrderType(str(order_type).upper())
        for stored in self._orders:
            if stored.order_id == order.order_id:
                stored.price, stored.quantity = order.price, order.quantity
                stored.trigger_price, stored.order_type = order.trigger_price, order.order_type
        return order

    def get_order_status(self, order: Order) -> Order:
        if order.order_id is not None:
            for stored in self._orders:
                if stored.order_id == order.order_id:
                    order.status = stored.status
                    order.filled_qty = stored.filled_qty
                    order.avg_price = stored.avg_price
        return order

    def get_order_detail(self, order_id: str) -> dict:
        for stored in self._orders:
            if stored.order_id == order_id:
                return stored.as_dict()
        return {}

    def get_orderbook(self) -> OrderBook:
        entries = tuple(
            OrderBookEntry(
                symbol=o.instrument.symbol,
                order_id=o.order_id or "",
                side=o.side.value if o.side else "",
                quantity=o.quantity,
                price=o.price or 0.0,
                status=o.status.value if o.status else "",
            )
            for o in self._orders
        )
        return OrderBook(entries=entries, timestamp=self._ts())

    def get_trade_book(self) -> TradeBook:
        entries = tuple(
            TradeBookEntry(
                symbol=o.instrument.symbol,
                trade_id=o.order_id or "",
                order_id=o.order_id or "",
                side=o.side.value if o.side else "",
                quantity=o.filled_qty or o.quantity,
                price=o.avg_price or 0.0,
            )
            for o in self._orders
            if o.status == OrderStatus.COMPLETED
        )
        return TradeBook(entries=entries, timestamp=self._ts())

    def order_report(self):
        return {
            "orders": len(self._orders),
            "orderbook": self.get_orderbook(),
            "tradebook": self.get_trade_book(),
        }

    @property
    def orders(self) -> list[Order]:
        return list(self._orders)

    def get_live_pnl(self) -> float:
        return 0.0

    def get_balance(self) -> float:
        return self._balance

    def get_positions(self):
        return list(self._positions.values())

    def get_holdings(self):
        return []

    def push_tick(self, instrument, price: float, side: str = "") -> None:
        tick = Tick(symbol=instrument.symbol, price=price, side=side, timestamp=self._ts(), kind="quote")
        self._dispatch_tick(instrument, tick)
