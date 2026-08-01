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

import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from ntrade.brokers.base import BrokerAdapter
from ntrade.brokers.capabilities import capability
from ntrade.brokers.dhan_auth import get_tradehull
from ntrade.brokers.dhan_auth_provider import DhanAuthProvider
from ntrade.brokers.dhan_mapper import (
    DhanMapper,
    chain_from_dhan_df,
    to_records,
    _f,
    _first_float,
    _first_int,
    _first_str,
)
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.domain.market.candles import CandleSeries
from ntrade.domain.market.depth import DepthLevel, MarketDepth
from ntrade.domain.market.quote import Quote
from ntrade.domain.orders.book import OrderBook, OrderBookEntry, TradeBook, TradeBookEntry
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
        self._transport = DhanTransport(self.tsl, clock=getattr(self, "_clock", None))
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
        self._ensure_tsl()  # Ensure token is fresh before API call
        names = [dhan_symbol(instrument)]
        ltp = 0.0
        # Dhan's get_ltp_data intermittently returns None / failure dicts
        # (observed live ~50% of calls); retry a few times before giving up.
        # Space the attempts so the retry actually beats Dhan's rate limiter.
        for attempt in range(3):
            try:
                data = self.tsl.get_ltp_data(names=names)
                candidate = float(data.get(names[0], 0.0) or 0.0)
                if candidate > 0:
                    ltp = candidate
                    break
            except Exception:
                pass
            if attempt < 2:
                time.sleep(0.2)
        if ltp <= 0:
            raise RuntimeError(
                f"get_quote failed for {instrument.symbol}: "
                f"LTP is 0 after 3 retries"
            )
        quote = Quote(ltp=ltp, bid=ltp, ask=ltp, timestamp=self._ts(now))
        try:
            qd = self.tsl.get_quote_data(names=names).get(names[0], {})
            quote = quote.with_update(
                high=_f(qd.get("high")), low=_f(qd.get("low")),
                open=_f(qd.get("open")), prev_close=_f(qd.get("close_price")),
                volume=int(qd.get("volume") or 0), oi=int(qd.get("open_interest") or 0),
            )
        except Exception:
            pass
        return quote

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
        try:
            dc = self.tsl.full_market_depth_data([(instrument.symbol, instrument.exchange)])
            key = f"{instrument.symbol.upper()}|{instrument.exchange.upper()}"
            client = dc.get(key) or next(iter(dc.values()), None)
            if client is None:
                return None

            import threading
            result: dict = {}

            def _read_frames():
                try:
                    result["frames"] = self.tsl.get_market_depth_df(client)
                except Exception as exc:  # noqa: BLE001
                    result["error"] = exc

            t = threading.Thread(target=_read_frames, daemon=True)
            t.start()
            t.join(timeout)
            if "frames" not in result:
                return None  # websocket never delivered a snapshot in time
            bid_df, ask_df = result["frames"]
            if bid_df is None or bid_df.empty:
                return None
            # Dhan's frames use bid_price/bid_qty (and ask_*) columns.
            bids = tuple(DepthLevel(price=_f(r.get("bid_price") or r.get("price")),
                                    quantity=int(r.get("bid_qty") or r.get("quantity") or 0))
                         for _, r in bid_df.iterrows())
            asks = tuple(DepthLevel(price=_f(r.get("ask_price") or r.get("price")),
                                    quantity=int(r.get("ask_qty") or r.get("quantity") or 0))
                         for _, r in ask_df.iterrows())
            return MarketDepth(symbol=instrument.symbol, bids=bids, asks=asks, timestamp=self._ts(now))
        except Exception:
            return None

    def get_historical(self, instrument, timeframe="5m", days=None, start=None, end=None) -> CandleSeries:
        tf = _dhan_timeframe(timeframe)  # raises ValueError for unsupported
        exchange = "INDEX" if instrument.KIND == "index" else instrument.exchange
        # Dhan's daily historical endpoint serves DAY OHLC for ALL segments
        # including MCX commodities (verified against the official docs); it is
        # only the library's intraday wrapper (get_historical_data) that rejects
        # FUT-type contracts client-side. Route DAY through the daily endpoint
        # exactly when the wrapper would block (mirrors its 'FUT' check);
        # everything else keeps the intraday path, which serves DAY fine.
        if tf == "DAY" and self._dhan_blocks_day(instrument):
            return self._historical_day_contract(instrument, days=days, start=start, end=end)
        try:
            df = self.tsl.get_historical_data(
                tradingsymbol=dhan_symbol(instrument), exchange=exchange, timeframe=tf
            )
        except Exception:
            return CandleSeries(pd.DataFrame(), symbol=instrument.symbol, timeframe=timeframe)
        df = _normalize_history(df)
        return CandleSeries(_filter_history(df, days=days, start=start, end=end, asof=self._ts()),
                            symbol=instrument.symbol, timeframe=timeframe)

    def _dhan_blocks_day(self, instrument) -> bool:
        """True when Dhan-Tradehull's intraday wrapper rejects DAY for a script.

        The wrapper raises when the instrument's type contains 'FUT' (FUTIDX,
        FUTCOM, OPTFUT); index/stock options (OPTIDX, OPTSTK) and equities are
        served DAY fine by the intraday path, so they must NOT be routed. We
        mirror the check via the instrument file rather than guessing by
        exchange. Symbols that don't resolve (e.g. commodity names like GOLD,
        which the wrapper looks up by SM_SYMBOL_NAME) default to blocked on
        commodity/future exchanges.
        """
        try:
            sym = dhan_symbol(instrument)
            idf = self.tsl.instrument_df
            # The wrapper filters by the MAPPED exchange (NFO→NSE, BFO→BSE)
            # because Dhan's instrument file stores index derivatives under the
            # cash-exchange id — mirror it exactly so the check is faithful.
            exch = _DAY_BLOCK_MAPPED_EXCHANGE.get(instrument.exchange, instrument.exchange)
            df = idf[((idf["SEM_TRADING_SYMBOL"] == sym) | (idf["SEM_CUSTOM_SYMBOL"] == sym))
                     & (idf["SEM_EXM_EXCH_ID"] == exch)]
            if df.empty:
                return instrument.exchange in ("MCX", "NFO", "BFO")
            return "FUT" in str(df.iloc[-1]["SEM_INSTRUMENT_NAME"])
        except Exception:
            return instrument.exchange in ("MCX", "NFO", "BFO")

    def _historical_day_contract(self, instrument, days=None, start=None, end=None) -> CandleSeries:
        """Daily candles for FUT-type contracts via Dhan's daily endpoint.

        Dhan's official daily historical API (POST /charts/historical) returns
        DAY OHLC for every segment, including MCX commodities and NFO futures.
        Only Dhan-Tradehull's intraday wrapper blocks them; this routes through
        get_long_term_historical_data, which resolves the contract itself
        (front-month for commodities) and fetches daily data in chunks. Returns
        an empty frame if the range has no data (weekend/holiday) or the request
        fails.
        """
        to_date = end if end is not None else self._ts().date()
        from_date = start if start is not None else (to_date - timedelta(days=days or 365))
        try:
            df = self.tsl.get_long_term_historical_data(
                tradingsymbol=dhan_symbol(instrument), exchange=instrument.exchange,
                timeframe="DAY", from_date=from_date, to_date=to_date,
            )
        except Exception:
            return CandleSeries(pd.DataFrame(), symbol=instrument.symbol, timeframe="1d")
        return CandleSeries(_filter_history(_normalize_history(df), days=days, start=start, end=end, asof=self._ts()),
                            symbol=instrument.symbol, timeframe="1d")

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
                result = self.tsl.get_option_chain(
                    Underlying=underlying.symbol, exchange=exchange,
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
            chain = _chain_from_dhan_df(underlying, chain_df, float(atm), asof=self._ts())
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
            records = _to_records(self.tsl.get_orderbook(debug="NO"))
            entries = tuple(
                OrderBookEntry(
                    symbol=str(r.get("tradingSymbol", r.get("symbol", ""))),
                    order_id=str(r.get("orderId", r.get("order_id", ""))),
                    side=str(r.get("transactionType", r.get("side", ""))).upper(),
                    quantity=int(r.get("quantity", r.get("qty", 0)) or 0),
                    price=float(r.get("price", r.get("pendingPrice", 0.0)) or 0.0),
                    status=str(r.get("status", r.get("orderState", ""))),
                    exchange=str(r.get("exchangeSegment", r.get("exchange", ""))),
                )
                for r in records
            )
            return OrderBook(entries=entries, timestamp=self._ts(now))
        except Exception:
            return OrderBook()

    def get_trade_book(self, *, now: datetime | None = None) -> TradeBook:
        """Trade book as a typed TradeBook domain object."""
        try:
            records = _to_records(self.tsl.get_trade_book(debug="NO"))
            entries = tuple(
                TradeBookEntry(
                    symbol=str(r.get("tradingSymbol", r.get("symbol", ""))),
                    trade_id=str(r.get("tradeId", r.get("trade_id", ""))),
                    order_id=str(r.get("orderId", r.get("order_id", ""))),
                    side=str(r.get("transactionType", r.get("side", ""))).upper(),
                    quantity=int(r.get("quantity", r.get("qty", 0)) or 0),
                    price=float(r.get("price", r.get("tradePrice", 0.0)) or 0.0),
                )
                for r in records
            )
            return TradeBook(entries=entries, timestamp=self._ts(now))
        except Exception:
            return TradeBook()

    def order_report(self):
        """Order report normalized to a dict of row-dict lists."""
        try:
            report = self.tsl.order_report()
        except Exception:
            return {}
        if isinstance(report, dict):
            return {k: (_to_records(v) if not isinstance(v, (str, int, float)) else v)
                    for k, v in report.items()}
        if isinstance(report, (tuple, list)):
            names = ("orders", "positions", "trades")
            return {names[i]: _to_records(part) for i, part in enumerate(report)}
        return {}

    def get_live_pnl(self) -> float:
        try:
            return float(self.tsl.get_live_pnl() or 0.0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------ portfolio
    def get_balance(self) -> float:
        self._ensure_tsl()  # Ensure token is fresh before balance fetch
        # Deliberately NOT collapsed to 0.0 on failure: a 0.0 balance is
        # indistinguishable from a genuine empty account, and silently zeroing
        # the account on a network blip would corrupt PositionSyncEngine.
        # Consumers that want defensive reads guard this themselves.
        return float(self.tsl.get_balance())

    def get_positions(self):
        """Return Position domain objects (Adapter: normalize broker API rows).

        Raises on a failed fetch rather than returning []: an empty list means
        "flat", so collapsing a transport error to [] would silently wipe the
        kernel's portfolio during reconciliation (PositionSyncEngine keeps the
        previous state when this raises)."""
        self._ensure_tsl()  # Ensure token is fresh before position fetch
        df = self.tsl.get_positions()
        return _positions_from_df(df)

    def get_holdings(self):
        """Return Holding domain objects (Adapter: normalize broker API rows)."""
        try:
            df = self.tsl.get_holdings()
        except Exception:
            return []
        return _holdings_from_df(df)

    # ------------------------------------------------------------ market data
    def get_expiry_list(self, instrument: "Instrument"):
        """Return the list of contract expiry dates for this underlying."""
        exchange = "INDEX" if instrument.KIND == "index" else "NFO"
        try:
            raw = self.tsl.get_expiry_list(Underlying=instrument.symbol, exchange=exchange)
        except Exception:
            return []
        dates = []
        for item in raw or []:
            try:
                dates.append(pd.to_datetime(item).date())
            except Exception:
                continue
        return dates

    def get_expiry_date(self, instrument: "Instrument", opt_fut: str = "OPTION") -> list:
        """Resolve contract expiry dates for an option/future script.

        Dhan's library takes opt_fut in ("OPTION", "FUTURE") and returns a
        LIST of expiry date strings — we normalize to a list[date] (empty on
        failure).
        """
        try:
            raw = self.tsl.get_expiry_date(Underlying=instrument.symbol, opt_fut=opt_fut)
        except Exception:
            return []
        dates = []
        for item in raw or []:
            try:
                dates.append(pd.to_datetime(item).date())
            except Exception:
                continue
        return dates

    def get_future_script(self, instrument: "Instrument", expiry: int):
        """Resolve the Dhan tradingsymbol for a future of this underlying."""
        try:
            return self.tsl.get_future_script(underlying=instrument.symbol, expiry=expiry)
        except Exception:
            return None

    def get_lot_size(self, instrument: "Instrument") -> int:
        """Fetch the lot size for a derivative script (options/futures)."""
        try:
            return int(self.tsl.get_lot_size(tradingsymbol=dhan_symbol(instrument)))
        except Exception:
            return 0

    def get_long_term_historical(self, instrument, timeframe="1d", from_date=None, to_date=None) -> pd.DataFrame:
        """Longer-dated history via Dhan's dedicated endpoint (dates required)."""
        tf = _dhan_timeframe(timeframe)
        exchange = "INDEX" if instrument.KIND == "index" else instrument.exchange
        try:
            df = self.tsl.get_long_term_historical_data(
                tradingsymbol=dhan_symbol(instrument), exchange=exchange,
                timeframe=tf, from_date=from_date, to_date=to_date,
            )
        except Exception:
            return pd.DataFrame()
        return _filter_history(_normalize_history(df), start=from_date, end=to_date)

    def get_ohlc(self, instrument: "Instrument") -> dict:
        """Intraday OHLC bundle from the tick-level endpoint."""
        try:
            data = self.tsl.get_ohlc_data(names=[dhan_symbol(instrument)])
            return dict(data.get(dhan_symbol(instrument), {}) or {})
        except Exception:
            return {}

    def get_start_date(self):
        try:
            return self.tsl.get_start_date()
        except Exception:
            return None

    def get_instrument_file(self):
        try:
            return self.tsl.get_instrument_file()
        except Exception:
            return None

    def get_instrument_metadata(self, instrument: "Instrument") -> dict:
        """Hydrate tick size / lot size / freeze qty from Dhan's instrument file.

        Falls back to the commodity FUTCOM row when the symbol only matches via
        SM_SYMBOL_NAME (e.g. 'GOLD'); returns {} when nothing matches.
        """
        try:
            sym = dhan_symbol(instrument)
            idf = self.tsl.instrument_df
            # Filter by exchange (mapped like the wrapper: NFO→NSE) so symbols
            # listed on multiple exchanges resolve deterministically.
            exch = _DAY_BLOCK_MAPPED_EXCHANGE.get(instrument.exchange, instrument.exchange)
            df = idf[((idf["SEM_TRADING_SYMBOL"] == sym) | (idf["SEM_CUSTOM_SYMBOL"] == sym))
                     & (idf["SEM_EXM_EXCH_ID"] == exch)]
            if df.empty:
                df = idf[(idf["SM_SYMBOL_NAME"].astype(str) == instrument.symbol.upper())
                         & (idf["SEM_INSTRUMENT_NAME"] == "FUTCOM")]
            if df.empty:
                return {}
            row = df.iloc[-1]
            tick = _first_float(row, "SEM_TICK_SIZE") or None
            lot = _first_int(row, "SEM_LOT_UNITS") or None
            frz = _first_int(row, "SEM_FREEZE_QTY") or None
            return {"tick_size": tick, "lot_size": lot, "freeze_qty": frz}
        except Exception:
            return {}


# ------------------------------------------------------------------ helpers
def _chain_from_dhan_df(underlying, df: pd.DataFrame, atm: float, expiry: date | None = None, asof: datetime | None = None) -> "OptionChain":
    """Build an OptionChain from a Dhan-style chain dataframe.

    Lives in the Dhan adapter (not the domain) so the domain layer stays
    broker-agnostic (mission: no broker-specific logic in domain objects).
    Dhan's chain frame has CE/PE columns per strike ("CE LTP", "CE OI", ...).
    """
    from ntrade.domain.analytics.greeks import Greeks
    from ntrade.domain.instruments.chain import OptionChain
    from ntrade.domain.instruments.derivatives import Option

    options: list[Option] = []
    for _, row in df.iterrows():
        strike = float(row["Strike Price"])
        strike_label = int(strike) if strike == int(strike) else strike
        for leg, prefix, otype in (("CE", "CE", "CE"), ("PE", "PE", "PE")):
            ltp_col = f"{prefix} LTP"
            if ltp_col not in df.columns or pd.isna(row.get(ltp_col)):
                continue
            opt = Option(
                symbol=f"{underlying.symbol} {strike_label} {leg}",
                exchange="NFO",
                strike=strike,
                expiry=expiry or (asof or datetime.now()).date(),
                option_type=otype,
                underlying_symbol=underlying.symbol,
                broker=underlying._broker,
            )
            opt._quote = opt._quote.with_update(
                ltp=float(row.get(ltp_col, 0) or 0),
                oi=int(row.get(f"{prefix} OI", 0) or 0),
                volume=int(row.get(f"{prefix} Volume", 0) or 0),
            )
            iv = float(row.get(f"{prefix} IV", 0) or 0)
            if iv:
                opt.set_greeks(Greeks(
                    delta=float(row.get(f"{prefix} Delta", 0) or 0),
                    gamma=float(row.get(f"{prefix} Gamma", 0) or 0),
                    theta=float(row.get(f"{prefix} Theta", 0) or 0),
                    vega=float(row.get(f"{prefix} Vega", 0) or 0),
                    iv=iv,
                ))
            options.append(opt)
    return OptionChain(underlying, options, expiry=expiry, atm_strike=atm, chain_df=df)


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _to_records(value):
    """Normalize a DataFrame / dict / list into a list of row dicts.

    Dhan's order/trade book endpoints return DataFrames (or dicts), which are
    ambiguous for truthiness (`bool(df)` raises) and unhelpful as raw returns.
    Symbol-keyed dicts (e.g. {"RELIANCE": {...}}) keep their key merged into
    each row as `symbol` so no data is silently lost.
    """
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    if isinstance(value, dict):
        if not value:
            return []
        if all(isinstance(v, (dict, list, tuple)) for v in value.values()):
            out = []
            for k, v in value.items():
                if isinstance(v, (list, tuple)):
                    out.extend([{**r, "symbol": k} if isinstance(r, dict) else r for r in v])
                else:
                    out.append({**v, "symbol": k})
            return out
        return [value]
    if isinstance(value, (list, tuple)):
        return [dict(v) if isinstance(v, dict) else v for v in value]
    return []


# Mirrors Dhan-Tradehull's `instrument_exchange` mapping used in its
# instrument-file lookups: NFO/BFO derivatives are filed under the cash
# exchange id, CUR under NSE.
_DAY_BLOCK_MAPPED_EXCHANGE = {"NFO": "NSE", "BFO": "BSE", "CUR": "NSE"}


_DHAN_TIMEFRAMES = {
    "1m": "1", "2m": "2", "3m": "3", "4m": "4", "5m": "5",
    "15m": "15", "25m": "25", "60m": "60", "1h": "60",
    "day": "DAY", "1d": "DAY", "daily": "DAY",
}


def _dhan_timeframe(tf: str) -> str:
    """Map a user timeframe to Dhan's interval string, raising on unsupported.

    Dhan only accepts ['1','2','3','4','5','15','25','60','DAY'] — there is no
    10-minute interval. Unknown values previously fell back to '5' silently,
    which returned the wrong data; now they raise so callers notice.
    """
    key = str(tf).strip().lower()
    if key not in _DHAN_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe {tf!r}; Dhan supports "
            f"1m/2m/3m/4m/5m/15m/25m/60m/DAY (not 10m)"
        )
    return _DHAN_TIMEFRAMES[key]


def _positions_from_df(df):
    """Map a Dhan positions DataFrame into Position domain objects (NaN-safe)."""
    from ntrade.domain.portfolio import Position
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        symbol = _first_str(r, "tradingSymbol", "tradingsymbol")
        if not symbol:
            continue
        out.append(Position(
            symbol=symbol,
            quantity=_position_quantity(r),
            avg_price=_first_float(r, "avgTradingPrice", "avgPrice"),
            ltp=_first_float(r, "ltp"),
            product=_first_str(r, "productType") or "MIS",
            exchange=_first_str(r, "exchangeSegment") or "NSE",
        ))
    return out


def _position_quantity(row) -> int:
    """netQty → quantity → (buyQty - sellQty) → 0, NaN-safe."""
    qty = _first_int(row, "netQty", "quantity")
    if qty == 0:
        qty = _first_int(row, "buyQty") - _first_int(row, "sellQty")
    return qty


def _holdings_from_df(df):
    """Map a Dhan holdings DataFrame into Holding domain objects (NaN-safe)."""
    from ntrade.domain.portfolio import Holding
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        symbol = _first_str(r, "tradingSymbol", "tradingsymbol")
        if not symbol:
            continue
        out.append(Holding(
            symbol=symbol,
            quantity=_first_int(r, "netQty", "quantity"),
            avg_price=_first_float(r, "avgTradingPrice", "avgPrice"),
            ltp=_first_float(r, "ltp"),
        ))
    return out


def _first_str(row, *keys) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            return str(v)
    return ""


def _first_int(row, *keys) -> int:
    for k in keys:
        v = row.get(k)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return 0


def _first_float(row, *keys) -> float:
    for k in keys:
        v = row.get(k)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    keep = [c for c in ("timestamp", "open", "high", "low", "close", "volume", "oi") if c in df.columns]
    return df[keep].reset_index(drop=True)


def _filter_history(df: pd.DataFrame, days=None, start=None, end=None, asof=None) -> pd.DataFrame:
    """Apply days/start/end filters the Dhan library does not support itself.

    Dhan returns tz-aware timestamps (IST), so the cutoffs are localized to the
    series' own timezone before comparing. ``asof`` anchors the ``days`` cutoff
    (injected clock for replay determinism; wall clock when not given).
    """
    if df.empty or "timestamp" not in df:
        return df
    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    tz = ts.dt.tz
    mask = pd.Series(True, index=df.index)

    def _cutoff(value):
        cut = pd.Timestamp(value)
        if tz is not None and cut.tzinfo is None:
            cut = cut.tz_localize(tz)
        return cut

    if start is not None:
        mask &= ts >= _cutoff(start)
    if end is not None:
        mask &= ts <= _cutoff(end)
    if days is not None:
        anchor = asof if asof is not None else datetime.now()
        mask &= ts >= _cutoff(anchor - timedelta(days=days))
    return df[mask].reset_index(drop=True)


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
