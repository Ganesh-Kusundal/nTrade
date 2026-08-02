"""DhanBroker — Dhan implementation of BrokerAdapter, wrapping Dhan-Tradehull.

Follows the dhan-tradehull skill patterns:
  - access_token / pin_totp auth via dhan_auth
  - get_ltp_data / get_quote_data for quotes
  - get_historical_data for history
  - get_option_chain / ATM_Strike_Selection for chains
  - order_placement with SEBI-compliant LIMIT orders for F&O

Provider decomposition (Phase E):
  - DhanAuthProvider: authentication lifecycle
  - DhanMapper: pure data mapping/normalization
  - DhanTransport: TSL API calls with retry/error handling
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from ntrade.brokers.base import BrokerAdapter
from ntrade.brokers.capabilities import capability
from ntrade.brokers.dhan_auth_provider import DhanAuthProvider
from ntrade.brokers.dhan_mapper import (
    DhanMapper,
    chain_from_dhan_df,
    _first_float,
    _first_int,
    _first_str,
)
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.execution.retry import RateLimiter
from ntrade.domain.market.candles import CandleSeries
from ntrade.domain.market.depth import MarketDepth
from ntrade.domain.market.quote import Quote
from ntrade.domain.orders.book import OrderBook, TradeBook
from ntrade.domain.orders.order import Order, OrderSide, OrderStatus, OrderType

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument


_SEBI_FNO_EXCHANGES = {"NFO", "BFO"}

# Re-export for backward compatibility (capabilities reference dhan_symbol)
dhan_symbol = DhanMapper.to_trading_symbol


class DhanBroker(BrokerAdapter):
    name = "dhan"

    def __init__(self, env_path: str = ".env", env: dict | None = None,
                 connect: bool = True, clock=None):
        super().__init__(clock=clock)
        self.env_path = env_path
        self.env = env
        self._auth = DhanAuthProvider(env_path=env_path, env=env)
        self._transport: DhanTransport | None = None
        self._mapper = DhanMapper()
        self.tsl = None  # backward compat — capabilities reference broker.tsl
        if connect:
            self.connect()

    def connect(self) -> "DhanBroker":
        self.tsl = self._auth.authenticate()
        self._rate_limiter = RateLimiter(calls_per_second=10.0)  # Dhan API ceiling
        self._transport = DhanTransport(
            self.tsl, rate_limiter=self._rate_limiter,
            clock=getattr(self, "_clock", None),
        )
        self._connected = True
        return self

    def _ensure_tsl(self):
        """Ensure the token is fresh before critical operations.

        Mirrors testTrade's pattern where DhanHttpClient calls
        token_manager.get_token() on every HTTP request. This checks
        if the token is expired/near-expiry and refreshes via PIN+TOTP
        if needed, then propagates the new tsl to the transport.

        Returns the current (or refreshed) Tradehull instance.
        Defensive: if _auth doesn't exist (test mock), skip refresh.
        """
        auth = getattr(self, "_auth", None)
        if auth is None:
            return self.tsl  # Test mock or uninitialized — skip refresh
        new_tsl = auth.refresh_if_needed()
        if new_tsl is not self.tsl:
            self.tsl = new_tsl
            if self._transport is not None:
                self._transport.tsl = new_tsl
        return self.tsl

    def stop(self) -> None:
        """Cancel the auth provider's proactive refresh timer (shutdown hook)."""
        auth = getattr(self, "_auth", None)
        if auth is not None and hasattr(auth, "stop"):
            auth.stop()

    def set_clock(self, clock) -> "DhanBroker":
        """Inject a TradingClock, propagating it to the transport too so the
        parity-critical paths (transport.get_quote / get_daily_historical)
        follow replay time rather than the wall clock."""
        self._clock = clock
        transport = getattr(self, "_transport", None)
        if transport is not None:
            transport._clock = clock
        return self

    # ------------------------------------------------------------ market data
    def get_quote(self, instrument: "Instrument", *, now: datetime | None = None) -> Quote:
        self._ensure_tsl()
        quote = self._transport.get_quote(dhan_symbol(instrument))
        if quote.ltp <= 0:
            raise RuntimeError(f"get_quote failed for {instrument.symbol}: LTP is 0")
        return quote.with_update(timestamp=self._ts(now))

    def get_depth(self, instrument: "Instrument", timeout: float = 5.0, *, now: datetime | None = None) -> MarketDepth | None:
        """20-level market depth for NSE/BSE/NFO/BFO (not indices).

        Dhan streams depth over a websocket and returns an OrderedDict keyed
        'SYMBOL|EXCH' -> depth_client; each client must be passed to
        get_market_depth_df individually (the dict itself has no get_data).
        The websocket snapshot can hang, so the frame read is bounded by a
        timeout thread and returns None on expiry.
        """
        # Dhan only supports depth for NSE/BSE/NFO/BFO — not indices.
        if instrument.KIND == "index":
            return None
        return self._transport.get_depth(instrument.symbol, instrument.exchange, timeout=timeout, now=now)

    def get_historical(self, instrument, timeframe="5m", days=None, start=None, end=None) -> CandleSeries:
        self._ensure_tsl()
        if DhanMapper.map_timeframe(timeframe) == "DAY" and self._dhan_blocks_day(instrument):
            return self._historical_day_contract(instrument, days=days, start=start, end=end)
        exchange = "INDEX" if instrument.KIND == "index" else instrument.exchange
        start_s = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else start
        end_s = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else end
        return self._transport.get_historical(
            dhan_symbol(instrument), exchange, timeframe, days=days, start=start_s, end=end_s,
        )

    def _dhan_blocks_day(self, instrument) -> bool:
        """True when Dhan-Tradehull's intraday wrapper rejects DAY for a script."""
        return self._transport.blocks_day(dhan_symbol(instrument), instrument.exchange)

    def _historical_day_contract(self, instrument, days=None, start=None, end=None) -> CandleSeries:
        start_s = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else start
        end_s = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else end
        df = self._transport.get_daily_historical(
            dhan_symbol(instrument), instrument.exchange, days=days, start=start_s, end=end_s,
        )
        return CandleSeries(df, symbol=instrument.symbol, timeframe="1d")

    def get_option_chain(self, underlying: "Instrument", expiry: int = 0, num_strikes: int = 10, **kwargs):
        """Fetch an option chain, falling back to the next expiry on failure.

        The Dhan library intermittently returns None / raises for a requested
        week (its internal LTP call fails transiently), so we retry the next
        expiries. `chain.expiry_index_used` records the index actually used
        (out-of-range attempts clamp to Dhan's last available expiry), and
        `chain.target_expiry` may be a placeholder — never assume the requested index.
        """
        from ntrade.domain.instruments.chain import OptionChain
        exchange = "INDEX" if underlying.KIND == "index" else "NFO"
        last_error: Exception | None = None
        for attempt in (expiry, expiry + 1, expiry + 2):
            try:
                result = self._transport.get_option_chain(
                    dhan_symbol(underlying), exchange,
                    expiry=attempt, num_strikes=num_strikes,
                )
            except Exception as exc:
                last_error = exc
                continue
            if result is None:
                last_error = RuntimeError(f"Dhan returned no option chain for {underlying.symbol} (expiry={attempt})")
                continue
            atm, chain_df = result
            if chain_df is None or chain_df.empty:
                last_error = RuntimeError(f"Dhan returned an empty option chain for {underlying.symbol} (expiry={attempt})")
                continue
            chain = chain_from_dhan_df(underlying, chain_df, float(atm), asof=self._ts())
            # Expose which contract was actually used so callers can verify
            # (the library does not return the real expiry date).
            chain.expiry_index_used = attempt
            # Resolve the REAL contract expiry from Dhan's expiry list so
            # chain.target_expiry / chain.expiries() are no longer placeholders.
            exp_dates = self.get_expiry_list(underlying)
            if exp_dates:
                real = exp_dates[min(attempt, len(exp_dates) - 1)]
                chain.target_expiry = real
                chain.expiry_list = exp_dates
                for opt in chain:
                    opt.expiry = real
            return chain
        raise RuntimeError(f"Dhan option chain failed for {underlying.symbol}: {last_error}")

    # ------------------------------------------------------------ orders
    def place_order(self, order: Order) -> Order:
        self._ensure_tsl()  # Ensure token is fresh before order placement
        # SEBI (Apr 2026): MARKET orders banned for F&O — force LIMIT.
        if order.instrument.exchange in _SEBI_FNO_EXCHANGES and order.order_type.value == "MARKET":
            order.order_type = order.order_type.__class__("LIMIT")
            ltp = order.instrument._quote.ltp
            if not ltp or ltp <= 0:
                raise RuntimeError(
                    f"Cannot convert MARKET order to LIMIT for {order.instrument.symbol}: LTP is {ltp}. "
                    "Refresh the instrument first (refresh() must run after market data is available)."
                )
            order.price = round(ltp * 1.02, 1) if order.side == OrderSide.BUY else round(ltp * 0.98, 1)
        # Bracket (BO) orders: Dhan's order_placement accepts no BO type — route
        # to its dedicated place_super_order API (entry + target + stop legs).
        if order.order_type.value == "BRACKET":
            try:
                order_id = self.tsl.place_super_order(
                    tradingsymbol=dhan_symbol(order.instrument),
                    exchange=order.instrument.exchange,
                    transaction_type=order.side.value, quantity=order.quantity,
                    order_type="LIMIT", trade_type=order.trade_type.value,
                    price=order.price, target_price=order.target_price or 0,
                    stop_loss_price=order.stop_loss_price or 0, trailing_jump=0,
                )
                order.order_id = str(order_id)
                order.status = OrderStatus.PENDING
                return order
            except Exception as exc:
                order.status = OrderStatus.REJECTED
                raise RuntimeError(f"Dhan bracket order rejected: {exc}") from exc
        txn = order.side.value
        try:
            order_id = self.tsl.order_placement(
                tradingsymbol=dhan_symbol(order.instrument),
                exchange=order.instrument.exchange,
                quantity=order.quantity,
                price=order.price,
                trigger_price=order.trigger_price,
                order_type=order.order_type.value,
                transaction_type=txn,
                trade_type=order.trade_type.value,
            )
            order.order_id = str(order_id)
            order.status = OrderStatus.PENDING
        except Exception as exc:
            order.status = OrderStatus.REJECTED
            raise RuntimeError(f"Dhan order rejected: {exc}") from exc
        return order

    def cancel_order(self, order: Order) -> Order:
        """Cancel a placed order via the Dhan OMS."""
        self._ensure_tsl()  # Ensure token is fresh before cancellation
        try:
            self.tsl.cancel_order(OrderID=order.order_id)
            order.status = OrderStatus.CANCELLED
        except Exception as exc:
            raise RuntimeError(f"Dhan cancel failed for {order.order_id}: {exc}") from exc
        return order

    def modify_order(self, order: Order, *, price=None, quantity=None, order_type=None, trigger_price=None) -> Order:
        """Modify an open order via the Dhan OMS."""
        self._ensure_tsl()  # Ensure token is fresh before modification
        ot = order_type if order_type is not None else order.order_type.value
        ot = ot.value if hasattr(ot, "value") else str(ot).upper()
        try:
            self.tsl.modify_order(
                order_id=order.order_id,
                order_type=ot,
                quantity=quantity if quantity is not None else order.quantity,
                price=price if price is not None else order.price,
                trigger_price=trigger_price if trigger_price is not None else order.trigger_price,
            )
        except Exception as exc:
            raise RuntimeError(f"Dhan modify failed for {order.order_id}: {exc}") from exc
        if price is not None:
            order.price = price
        if quantity is not None:
            order.quantity = quantity
        if trigger_price is not None:
            order.trigger_price = trigger_price
        if order_type is not None:
            order.order_type = OrderType(ot)
        return order

    def get_order_status(self, order: Order) -> Order:
        """Refresh order status/fills from Dhan."""
        try:
            status = str(self.tsl.get_order_status(orderid=order.order_id)).upper()
        except Exception:
            return order
        _DHAN_STATUS = {
            "PENDING": OrderStatus.PENDING, "TRANSIT": OrderStatus.PENDING,
            "COMPLETE": OrderStatus.COMPLETED, "TRADED": OrderStatus.COMPLETED,
            "PARTIAL": OrderStatus.PARTIALLY_FILLED, "CANCELLED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED, "EXPIRED": OrderStatus.CANCELLED,
        }
        order.status = _DHAN_STATUS.get(status, OrderStatus.PENDING)
        try:
            detail = self.get_order_detail(order.order_id)
            order.filled_qty = int(detail.get("filled_qty", order.filled_qty) or order.filled_qty)
            order.avg_price = float(detail.get("avg_price", order.avg_price) or order.avg_price)
        except Exception:
            pass
        return order

    def get_order_detail(self, order_id: str) -> dict:
        """Return normalized order detail dict from Dhan."""
        try:
            raw = self.tsl.get_order_detail(orderid=order_id, debug="NO") or {}
        except Exception:
            return {}
        return {
            "order_id": str(raw.get("orderId") or raw.get("OrderID") or order_id),
            "status": _first_str(raw, "orderStatus", "OrderStatus", "status"),
            "filled_qty": _first_int(raw, "filledQty", "filled_qty", "qty"),
            "avg_price": _first_float(raw, "avgPrice", "averagePrice", "avg_price"),
            "price": _first_float(raw, "price", "OrderPrice"),
            "quantity": _first_int(raw, "quantity", "Qty"),
        }

    def get_executed_price(self, order: Order) -> float:
        """Average execution price of a completed order."""
        try:
            return float(self.tsl.get_executed_price(orderid=order.order_id))
        except Exception:
            return float(order.avg_price or 0.0)

    def get_executed_price_and_time(self, order: Order):
        """(price, exchange_time) for a completed order."""
        try:
            price, ts = self.tsl.get_executed_price_and_time(orderid=order.order_id)
            return float(price), str(ts)
        except Exception:
            return float(order.avg_price or 0.0), ""

    def get_orderbook(self, *, now: datetime | None = None) -> OrderBook:
        """Order book as a typed OrderBook domain object."""
        try:
            return DhanMapper.normalize_orderbook(
                self._transport.get_orderbook(), now=self._ts(now),
            )
        except Exception:
            return OrderBook()

    def get_trade_book(self, *, now: datetime | None = None) -> TradeBook:
        """Trade book as a typed TradeBook domain object."""
        try:
            return DhanMapper.normalize_tradebook(
                self._transport.get_trade_book(), now=self._ts(now),
            )
        except Exception:
            return TradeBook()

    def order_report(self):
        """Order report normalized to a dict of row-dict lists."""
        return self._transport.order_report()

    def get_live_pnl(self) -> float:
        return self._transport.get_live_pnl()

    # ------------------------------------------------------------ portfolio
    def get_balance(self) -> float:
        self._ensure_tsl()  # Ensure token is fresh before balance fetch
        # Deliberately NOT collapsed to 0.0 on failure: a 0.0 balance is
        # indistinguishable from a genuine empty account, and silently zeroing
        # the account on a network blip would corrupt PositionSyncEngine.
        # Consumers that want defensive reads guard this themselves.
        return self._transport.get_balance()

    def get_positions(self):
        """Return Position domain objects (Adapter: normalize broker API rows).

        Raises on a failed fetch rather than returning []: an empty list means
        "flat", so collapsing a transport error to [] would silently wipe the
        kernel's portfolio during reconciliation (PositionSyncEngine keeps the
        previous state when this raises)."""
        self._ensure_tsl()  # Ensure token is fresh before position fetch
        return self._transport.get_positions()

    def get_holdings(self):
        """Return Holding domain objects (Adapter: normalize broker API rows)."""
        try:
            return self._transport.get_holdings()
        except Exception:
            return []

    # ------------------------------------------------------------ market data
    def get_expiry_list(self, instrument: "Instrument"):
        """Return the list of contract expiry dates for this underlying."""
        exchange = "INDEX" if instrument.KIND == "index" else "NFO"
        try:
            return self._transport.get_expiry_list(instrument.symbol, exchange)
        except Exception:
            return []

    def get_expiry_date(self, instrument: "Instrument", opt_fut: str = "OPTION") -> list:
        """Resolve contract expiry dates for an option/future script.

        Dhan's library takes opt_fut in ("OPTION", "FUTURE") and returns a
        LIST of expiry date strings — we normalize to a list[date] (empty on
        failure).
        """
        try:
            return self._transport.get_expiry_date(instrument.symbol, opt_fut)
        except Exception:
            return []

    def get_future_script(self, instrument: "Instrument", expiry: int):
        """Resolve the Dhan tradingsymbol for a future of this underlying."""
        try:
            return self._transport.get_future_script(instrument.symbol, expiry)
        except Exception:
            return None

    def get_lot_size(self, instrument: "Instrument") -> int:
        """Fetch the lot size for a derivative script (options/futures)."""
        try:
            return self._transport.get_lot_size(dhan_symbol(instrument))
        except Exception:
            return 0

    def get_long_term_historical(self, instrument, timeframe="1d", from_date=None, to_date=None) -> pd.DataFrame:
        """Longer-dated history via Dhan's dedicated endpoint (dates required)."""
        self._ensure_tsl()
        exchange = "INDEX" if instrument.KIND == "index" else instrument.exchange
        from_s = from_date.strftime("%Y-%m-%d") if hasattr(from_date, "strftime") else from_date
        to_s = to_date.strftime("%Y-%m-%d") if hasattr(to_date, "strftime") else to_date
        return self._transport.get_long_term_historical(
            dhan_symbol(instrument), exchange, timeframe, from_date=from_s, to_date=to_s,
        )

    def get_ohlc(self, instrument: "Instrument") -> dict:
        """Intraday OHLC bundle from the tick-level endpoint."""
        try:
            return self._transport.get_ohlc(dhan_symbol(instrument))
        except Exception:
            return {}

    def get_start_date(self):
        try:
            return self._transport.get_start_date()
        except Exception:
            return None

    def get_instrument_file(self):
        try:
            return self._transport.get_instrument_file()
        except Exception:
            return None

    def get_instrument_metadata(self, instrument: "Instrument") -> dict:
        """Hydrate tick size / lot size / freeze qty from Dhan's instrument file.

        Falls back to the commodity FUTCOM row when the symbol only matches via
        SM_SYMBOL_NAME (e.g. 'GOLD'); returns {} when nothing matches.
        """
        try:
            return self._transport.get_instrument_metadata(
                dhan_symbol(instrument), instrument.exchange,
                underlying_symbol=instrument.symbol,
            )
        except Exception:
            return {}


# ------------------------------------------------------------------ capabilities
@capability("depth20", brokers=("dhan",))
def _depth20(instrument, levels: int = 20) -> MarketDepth:
    broker = instrument.broker_adapter
    if not isinstance(broker, DhanBroker):
        raise AttributeError("depth20 requires a DhanBroker")
    depth = broker.get_depth(instrument)
    if depth is None:
        raise RuntimeError("Could not fetch depth")
    return MarketDepth(
        symbol=depth.symbol,
        bids=tuple(depth.bids[:levels]),
        asks=tuple(depth.asks[:levels]),
        timestamp=depth.timestamp,
    )


@capability("margin_calculator", brokers=("dhan",))
def _margin_calculator(instrument, quantity: int, transaction_type: str, trade_type: str = "MIS",
                       price: float = 0, trigger_price: float = 0, exchange: str | None = None):
    """Estimate margin required for a potential order on this instrument."""
    broker = instrument.broker_adapter
    return broker.tsl.margin_calculator(
        tradingsymbol=dhan_symbol(instrument),
        exchange=exchange or instrument.exchange,
        transaction_type=transaction_type.upper(),
        quantity=quantity, trade_type=trade_type.upper(),
        price=price, trigger_price=trigger_price,
    )


@capability("kill_switch", brokers=("dhan",))
def _kill_switch(instrument, action: str = "DEACTIVATE"):
    """Activate/deactivate the broker kill switch (emergency flat)."""
    broker = instrument.broker_adapter
    return broker.tsl.kill_switch(action=action)


@capability("enable_pnl_based_exit", brokers=("dhan",))
def _enable_pnl_based_exit(instrument, profit_value=None, loss_value=None,
                           product_types=("INTRADAY", "DELIVERY"),
                           enable_kill_switch: bool = False, timeout: int = 10):
    """Enable broker-side auto-exit at profit/loss thresholds."""
    broker = instrument.broker_adapter
    return broker.tsl.enable_pnl_based_exit(
        profit_value=profit_value, loss_value=loss_value,
        product_types=product_types, enable_kill_switch=enable_kill_switch, timeout=timeout,
    )


@capability("expiry_list", brokers=("dhan",))
def _expiry_list(instrument):
    """Real contract expiries for this underlying (list[date])."""
    broker = instrument.broker_adapter
    return broker.get_expiry_list(instrument)


@capability("lot_size", brokers=("dhan",))
def _lot_size(instrument):
    """Contract lot size for a derivative script."""
    broker = instrument.broker_adapter
    return broker.get_lot_size(instrument)


@capability("future_script", brokers=("dhan",))
def _future_script(instrument, expiry: int):
    """Resolve the Dhan tradingsymbol for this underlying's future at expiry."""
    broker = instrument.broker_adapter
    return broker.get_future_script(instrument, expiry)


@capability("long_term_history", brokers=("dhan",))
def _long_term_history(instrument, timeframe="1d", from_date=None, to_date=None):
    """Longer-dated history (requires explicit date range)."""
    broker = instrument.broker_adapter
    return broker.get_long_term_historical(instrument, timeframe=timeframe,
                                           from_date=from_date, to_date=to_date)


@capability("ohlc", brokers=("dhan",))
def _ohlc(instrument):
    """Tick-level intraday OHLC bundle for this instrument."""
    broker = instrument.broker_adapter
    return broker.get_ohlc(instrument)


@capability("start_date", brokers=("dhan",))
def _start_date(instrument):
    """Earliest available date for this underlying on Dhan."""
    broker = instrument.broker_adapter
    return broker.get_start_date()


@capability("instrument_file", brokers=("dhan",))
def _instrument_file(instrument):
    """Path/location of Dhan's instrument master file."""
    broker = instrument.broker_adapter
    return broker.get_instrument_file()


# ------------------------------------------------------------------ advanced orders
@capability("place_super_order", brokers=("dhan",))
def _place_super_order(instrument, side: str, quantity: int, order_type: str = "LIMIT",
                       trade_type: str = "MIS", price: float = 0,
                       target_price: float = 0, stop_loss_price: float = 0, trailing_jump: float = 0):
    """Bracket-style super order (entry + target + stop legs) in one call.

    NOTE: Dhan's place_super_order accepts NO trigger_price — entry pricing is
    `price`, exit legs are target_price/stop_loss_price.
    """
    broker = instrument.broker_adapter
    return broker.tsl.place_super_order(
        tradingsymbol=dhan_symbol(instrument), exchange=instrument.exchange,
        transaction_type=side.upper(), quantity=quantity,
        order_type=order_type.upper(), trade_type=trade_type.upper(),
        price=price, target_price=target_price,
        stop_loss_price=stop_loss_price, trailing_jump=trailing_jump,
    )


@capability("place_slice_order", brokers=("dhan",))
def _place_slice_order(instrument, side: str, quantity: int, order_type: str = "LIMIT",
                       trade_type: str = "MIS", price: float = 0, trigger_price: float = 0,
                       after_market_order: bool = False):
    """Iceberg-style slice order."""
    broker = instrument.broker_adapter
    return broker.tsl.place_slice_order(
        tradingsymbol=dhan_symbol(instrument), exchange=instrument.exchange,
        transaction_type=side.upper(), quantity=quantity,
        order_type=order_type.upper(), trade_type=trade_type.upper(),
        price=price, trigger_price=trigger_price,
        after_market_order=after_market_order,
    )


@capability("place_conditional_trigger", brokers=("dhan",))
def _place_conditional_trigger(instrument, side: str, quantity: int, price: float, trigger_price: float,
                               order_type: str = "LIMIT", trade_type: str = "MIS",
                               comparison_type: str = "PRICE_WITH_VALUE", operator: str | None = None,
                               comparing_value: float | None = None, time_frame: str = "DAY",
                               indicator_name: str | None = None, frequency: str = "ONCE",
                               user_note: str = "", timeout: int = 10):
    """Server-side conditional/alert order."""
    broker = instrument.broker_adapter
    return broker.tsl.place_conditional_trigger(
        tradingsymbol=dhan_symbol(instrument), exchange=instrument.exchange,
        quantity=quantity, price=price, trigger_price=trigger_price,
        order_type=order_type.upper(), transaction_type=side.upper(), trade_type=trade_type.upper(),
        comparison_type=comparison_type, operator=operator, time_frame=time_frame,
        comparing_value=comparing_value, indicator_name=indicator_name,
        frequency=frequency, user_note=user_note, timeout=timeout,
    )


@capability("place_forever_order", brokers=("dhan",))
def _place_forever_order(instrument, side: str, quantity: int, order_type: str = "LIMIT",
                         trade_type: str = "MIS", price: float = 0, trigger_price: float = 0,
                         order_flag: str = "SINGLE", quantity_1: int = 0, price_1: float = 0,
                         trigger_price_1: float = 0):
    """GTT-style forever order (persistent until filled or cancelled)."""
    broker = instrument.broker_adapter
    return broker.tsl.place_forever_order(
        tradingsymbol=dhan_symbol(instrument), exchange=instrument.exchange,
        transaction_type=side.upper(), quantity=quantity,
        order_type=order_type.upper(), trade_type=trade_type.upper(),
        price=price, trigger_price=trigger_price, order_flag=order_flag,
        quantity_1=quantity_1, price_1=price_1, trigger_price_1=trigger_price_1,
    )


@capability("cancel_all_orders", brokers=("dhan",))
def _cancel_all_orders(instrument):
    """Cancel every open order on the account."""
    broker = instrument.broker_adapter
    return broker.tsl.cancel_all_orders()


@capability("get_super_orders", brokers=("dhan",))
def _get_super_orders(instrument):
    """All bracket/super orders on the account."""
    broker = instrument.broker_adapter
    return broker.tsl.get_super_orders()


@capability("modify_super_order", brokers=("dhan",))
def _modify_super_order(instrument, order_id: str, leg_name: str, quantity: int, order_type: str,
                        price: float = 0, target_price: float = 0, stop_loss_price: float = 0,
                        trailing_jump: float = 0):
    """Modify a leg of an existing super order."""
    broker = instrument.broker_adapter
    return broker.tsl.modify_super_order(
        order_id=order_id, leg_name=leg_name, quantity=quantity,
        order_type=order_type.upper(), price=price, target_price=target_price,
        stop_loss_price=stop_loss_price, trailing_jump=trailing_jump,
    )


@capability("cancel_super_order", brokers=("dhan",))
def _cancel_super_order(instrument, order_id: str, leg_name: str = "ENTRY_LEG"):
    """Cancel a super order (or one of its legs)."""
    broker = instrument.broker_adapter
    return broker.tsl.cancel_super_order(order_id=order_id, leg_name=leg_name)


@capability("get_forever_orders", brokers=("dhan",))
def _get_forever_orders(instrument):
    """All GTT / forever orders on the account."""
    broker = instrument.broker_adapter
    return broker.tsl.get_forever_orders()


@capability("modify_forever_order", brokers=("dhan",))
def _modify_forever_order(instrument, order_id: str, order_flag: str, order_type: str, quantity: int,
                          price: float, trigger_price: float = 0, disclosed_quantity: int = 0,
                          validity: str = "DAY", leg_name: str = "TARGET_LEG"):
    """Modify a GTT / forever order."""
    broker = instrument.broker_adapter
    return broker.tsl.modify_forever_order(
        order_id=order_id, order_flag=order_flag, order_type=order_type.upper(),
        quantity=quantity, price=price, trigger_price=trigger_price,
        disclosed_quantity=disclosed_quantity, validity=validity, leg_name=leg_name,
    )


@capability("cancel_forever_order", brokers=("dhan",))
def _cancel_forever_order(instrument, order_id: str):
    """Cancel a GTT / forever order."""
    broker = instrument.broker_adapter
    return broker.tsl.cancel_forever_order(order_id=order_id)


@capability("get_conditional_triggers", brokers=("dhan",))
def _get_conditional_triggers(instrument, timeout: int = 10):
    broker = instrument.broker_adapter
    return broker.tsl.get_all_conditional_triggers(timeout=timeout)


@capability("get_conditional_trigger", brokers=("dhan",))
def _get_conditional_trigger(instrument, alert_id: str, timeout: int = 10):
    broker = instrument.broker_adapter
    return broker.tsl.get_conditional_trigger_by_id(alert_id=alert_id, timeout=timeout)


@capability("delete_conditional_trigger", brokers=("dhan",))
def _delete_conditional_trigger(instrument, alert_id: str, timeout: int = 10):
    broker = instrument.broker_adapter
    return broker.tsl.delete_conditional_trigger(alert_id=alert_id, timeout=timeout)


@capability("atm_strike", brokers=("dhan",))
def _atm_strike(instrument, expiry: int):
    """Resolve the ATM strike for an underlying+expiry index."""
    broker = instrument.broker_adapter
    return broker.tsl.ATM_Strike_Selection(Underlying=instrument.symbol, Expiry=expiry)


@capability("itm_strike", brokers=("dhan",))
def _itm_strike(instrument, expiry: int, count: int = 1):
    broker = instrument.broker_adapter
    return broker.tsl.ITM_Strike_Selection(Underlying=instrument.symbol, Expiry=expiry, ITM_count=count)


@capability("otm_strike", brokers=("dhan",))
def _otm_strike(instrument, expiry: int, count: int = 1):
    broker = instrument.broker_adapter
    return broker.tsl.OTM_Strike_Selection(Underlying=instrument.symbol, Expiry=expiry, OTM_count=count)


@capability("expired_option_data", brokers=("dhan",))
def _expired_option_data(instrument, interval: int, expiry_flag: str, expiry_code: int,
                         strike: str = "ATM", option_type: str = "CALL",
                         required_data=None, from_date: str = "", to_date: str = ""):
    """Historical OHLC for an already-expired option contract."""
    broker = instrument.broker_adapter
    return broker.tsl.get_expired_option_data(
        tradingsymbol=dhan_symbol(instrument), exchange=instrument.exchange,
        interval=interval, expiry_flag=expiry_flag, expiry_code=expiry_code,
        strike=strike, option_type=option_type, required_data=required_data,
        from_date=from_date, to_date=to_date,
    )


@capability("exchange_time", brokers=("dhan",))
def _exchange_time(instrument, orderid: str):
    """Exchange-side timestamp of an order."""
    broker = instrument.broker_adapter
    return broker.tsl.get_exchange_time(orderid=orderid)
