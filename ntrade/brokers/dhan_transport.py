"""DhanTransport — wraps Tradehull API calls with rate gating, retry and error handling.

Sits between DhanBroker and the raw Tradehull library.  Each method adds:
  - a quota-classified choke point: every ``self._tsl.*`` call passes through
    ``_invoke(quota, fn)`` which acquires the session's ``BrokerRateGate``
    before firing (T-026) — Quote 1/s, Data 5/s, Order 10/s, NonTrading 20/s
  - retry logic for transient failures (LTP), never retrying DH-904 (B-011)
  - graceful fallback to empty/zero on non-critical endpoints
  - consistent error propagation for critical endpoints (orders) and for
    rate-limit failures (a DH-904 surfaces as ``RateLimited``, never as an
    empty/None success)

The transport holds a reference to the authenticated Tradehull instance
(provided by DhanAuthProvider) and delegates all actual API calls to it.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

from ntrade.execution.retry import RetryPolicy
from ntrade.execution.rate_limit import Quota, RateLimited, is_rate_limited
from ntrade.brokers.dhan_mapper import (
    DAY_BLOCK_MAPPED_EXCHANGE,
    DhanMapper,
    to_records,
    _f,
    _first_float,
    _first_int,
    _first_str,
)
from ntrade.domain.market.candles import CandleSeries
from ntrade.domain.market.depth import MarketDepth
from ntrade.domain.market.quote import Quote
from ntrade.domain.orders.book import OrderBook, TradeBook

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument
    from ntrade.domain.orders.order import Order


class BrokerDataError(RuntimeError):
    """A market-data read failed or returned a degenerate value after retries.

    Raised (never collapsed to a silent zero) so a dead broker or a zero LTP
    cannot feed corrupt prices into PnL / risk computation (B-005 contract).
    """


class DhanTransport:
    """Wraps Tradehull API calls with rate gating, retry and normalization.

    Constructed with an authenticated Tradehull instance; the DhanBroker
    composes this alongside DhanAuthProvider and DhanMapper.  ``gate`` is the
    session's shared ``BrokerRateGate`` (None ⇒ unthrottled, paper/test-safe).
    """

    def __init__(self, tsl: Any, retry_policy: RetryPolicy | None = None,
                 gate=None, clock=None):
        self._tsl = tsl
        self._mapper = DhanMapper()
        self._retry_policy = retry_policy or RetryPolicy()
        self._gate = gate  # optional shared BrokerRateGate (T-026)
        self._clock = clock  # optional TradingClock — zero-parity timestamps

    def _ts(self, now=None):
        """Resolve a timestamp: explicit ``now`` > injected clock > wall clock."""
        if now is not None:
            return now
        if self._clock is not None:
            return self._clock.now()
        return datetime.now()

    @property
    def tsl(self) -> Any:
        return self._tsl

    @tsl.setter
    def tsl(self, value: Any) -> None:
        self._tsl = value

    # ---- rate gate --------------------------------------------------------

    def _invoke(self, quota: Quota, fn: Callable[[], Any], *, retryable: bool = False) -> Any:
        """The single choke point: acquire the quota window, run *fn*, and on a
        rate-limit failure back the class off before re-raising.

        Every outbound ``self._tsl.*`` call in this class is wrapped in
        ``_invoke`` with its quota class (T-026).  ``retryable`` is reserved
        for narrow, proven-flaky reads that may retry *after* the gate.

        A raw exception whose text matches a rate-limit signal (DH-904 /
        Rate_Limit / 429) is normalised into a typed :class:`RateLimited` here
        — so callers' ``except Exception`` fallbacks can never swallow a quota
        violation as a silent empty/None success.
        """
        if self._gate is not None:
            self._gate.acquire(quota)
        try:
            return fn()
        except RateLimited as exc:
            if self._gate is not None:
                self._gate.penalize(quota, exc.retry_after or 1.0)
            raise
        except Exception as exc:
            if is_rate_limited(exc):
                rl = RateLimited(quota, message=str(exc))
                if self._gate is not None:
                    self._gate.penalize(quota, rl.retry_after or 1.0)
                raise rl from exc
            raise

    # ---- market data -------------------------------------------------------

    def get_ltp(self, symbol: str) -> float:
        """Fetch LTP with retry (Dhan's endpoint is flaky ~50% of calls).

        Raises :class:`BrokerDataError` when every retry fails or the broker
        returns a zero price — a 0.0 LTP is indistinguishable from "no data"
        and would silently corrupt downstream PnL/risk (B-005 contract).
        A DH-904 surfaces as :class:`RateLimited` (never retried, B-011).
        """
        names = [symbol]

        def _try_ltp() -> float:
            data = self._invoke(
                Quota.QUOTE, lambda: self._tsl.get_ltp_data(names=names),
            )
            candidate = float(data.get(names[0], 0.0) or 0.0)
            if candidate > 0:
                return candidate
            raise ValueError("LTP is 0")

        try:
            return self._retry_policy.execute(_try_ltp)
        except RateLimited:
            raise
        except Exception as exc:
            raise BrokerDataError(
                f"LTP fetch failed for {symbol} after retries: {exc}"
            ) from exc

    def get_quote(self, symbol: str) -> Quote:
        """Fetch full quote with LTP retry + optional quote-data enrichment.

        Consumes two QUOTE tokens (get_ltp_data + get_quote_data) — effective
        quote rate is 0.5/s under Dhan's 1/s Quote window; documented, not a
        bug.
        """
        ltp = self.get_ltp(symbol)
        quote = DhanMapper.normalize_quote(ltp, now=self._ts())
        try:
            qd = self._invoke(
                Quota.QUOTE, lambda: self._tsl.get_quote_data(names=[symbol]),
            ).get(symbol, {})
            quote = quote.with_update(
                high=_f(qd.get("high")), low=_f(qd.get("low")),
                open=_f(qd.get("open")), prev_close=_f(qd.get("close_price")),
                volume=int(qd.get("volume") or 0), oi=int(qd.get("open_interest") or 0),
            )
        except RateLimited:
            raise
        except Exception:
            pass
        return quote

    def get_depth(self, symbol: str, exchange: str, timeout: float = 5.0, *, now: datetime | None = None) -> MarketDepth | None:
        """20-level market depth via websocket snapshot (timeout-bounded)."""
        try:
            dc = self._invoke(
                Quota.DATA,
                lambda: self._tsl.full_market_depth_data([(symbol, exchange)]),
            )
            key = f"{symbol.upper()}|{exchange.upper()}"
            client = dc.get(key) or next(iter(dc.values()), None)
            if client is None:
                return None
            result: dict = {}

            def _read_frames():
                try:
                    result["frames"] = self._invoke(
                        Quota.DATA, lambda: self._tsl.get_market_depth_df(client),
                    )
                except Exception as exc:
                    result["error"] = exc

            t = threading.Thread(target=_read_frames, daemon=True)
            t.start()
            t.join(timeout)
            if "frames" not in result:
                return None
            bid_df, ask_df = result["frames"]
            if bid_df is None or bid_df.empty:
                return None
            return DhanMapper.normalize_depth(symbol, bid_df, ask_df, now=now or self._ts())
        except RateLimited:
            raise
        except Exception:
            return None

    def get_historical(
        self, symbol: str, exchange: str, timeframe: str,
        days: int | None = None, start: str | None = None, end: str | None = None,
    ) -> CandleSeries:
        """Fetch historical data with normalization and filtering.

        A DH-904 raises :class:`RateLimited` (never an empty ``CandleSeries``,
        which would look like "no bars" to a strategy).
        """
        tf = DhanMapper.map_timeframe(timeframe)
        try:
            df = self._invoke(
                Quota.DATA,
                lambda: self._tsl.get_historical_data(
                    tradingsymbol=symbol, exchange=exchange, timeframe=tf,
                ),
            )
        except RateLimited:
            raise
        except Exception:
            return CandleSeries(pd.DataFrame(), symbol=symbol, timeframe=timeframe)
        df = DhanMapper.normalize_history(df)
        return CandleSeries(DhanMapper.filter_history(df, days=days, start=start, end=end, asof=self._ts()), symbol=symbol, timeframe=timeframe)

    def get_long_term_historical(
        self, symbol: str, exchange: str, timeframe: str = "DAY",
        from_date: str | None = None, to_date: str | None = None,
    ) -> pd.DataFrame:
        """Longer-dated history via Dhan's dedicated endpoint."""
        tf = DhanMapper.map_timeframe(timeframe)
        try:
            df = self._invoke(
                Quota.DATA,
                lambda: self._tsl.get_long_term_historical_data(
                    tradingsymbol=symbol, exchange=exchange,
                    timeframe=tf, from_date=from_date, to_date=to_date,
                ),
            )
        except RateLimited:
            raise
        except Exception:
            return pd.DataFrame()
        return DhanMapper.filter_history(
            DhanMapper.normalize_history(df), start=from_date, end=to_date,
            asof=self._ts(),
        )

    def get_daily_historical(
        self, symbol: str, exchange: str,
        days: int | None = None, start: str | None = None, end: str | None = None,
    ) -> pd.DataFrame:
        """Daily candles via Dhan's daily endpoint (for FUT-type contracts)."""
        to_date = end if end is not None else self._ts().date()
        from_date = start if start is not None else (to_date - timedelta(days=days or 365))
        try:
            df = self._invoke(
                Quota.DATA,
                lambda: self._tsl.get_long_term_historical_data(
                    tradingsymbol=symbol, exchange=exchange,
                    timeframe="DAY", from_date=from_date, to_date=to_date,
                ),
            )
        except RateLimited:
            raise
        except Exception:
            return pd.DataFrame()
        return DhanMapper.filter_history(
            DhanMapper.normalize_history(df), days=days, start=start, end=end,
            asof=self._ts(),
        )

    # ---- option chain ------------------------------------------------------

    def get_option_chain(
        self, underlying: str, exchange: str,
        expiry: int = 0, num_strikes: int = 10,
    ):
        """Fetch raw option chain result from Dhan (atm, chain_df)."""
        return self._invoke(
            Quota.DATA,
            lambda: self._tsl.get_option_chain(
                Underlying=underlying, exchange=exchange,
                expiry=expiry, num_strikes=num_strikes,
            ),
        )

    def get_expiry_list(self, underlying: str, exchange: str) -> list[date]:
        """Return contract expiry dates for an underlying."""
        try:
            raw = self._invoke(
                Quota.NON_TRADING,
                lambda: self._tsl.get_expiry_list(Underlying=underlying, exchange=exchange),
            )
        except RateLimited:
            raise
        except Exception:
            return []
        dates = []
        for item in raw or []:
            try:
                dates.append(pd.to_datetime(item).date())
            except Exception:
                continue
        return dates

    def get_expiry_date(self, underlying: str, opt_fut: str = "OPTION") -> list[date]:
        """Resolve contract expiry dates for an option/future script."""
        try:
            raw = self._invoke(
                Quota.NON_TRADING,
                lambda: self._tsl.get_expiry_date(Underlying=underlying, opt_fut=opt_fut),
            )
        except RateLimited:
            raise
        except Exception:
            return []
        dates = []
        for item in raw or []:
            try:
                dates.append(pd.to_datetime(item).date())
            except Exception:
                continue
        return dates

    def get_future_script(self, underlying: str, expiry: int) -> str | None:
        try:
            return self._invoke(
                Quota.NON_TRADING,
                lambda: self._tsl.get_future_script(underlying=underlying, expiry=expiry),
            )
        except RateLimited:
            raise
        except Exception:
            return None

    def get_lot_size(self, symbol: str) -> int:
        try:
            return int(self._invoke(
                Quota.NON_TRADING, lambda: self._tsl.get_lot_size(tradingsymbol=symbol),
            ))
        except RateLimited:
            raise
        except Exception:
            return 0

    def get_ohlc(self, symbol: str) -> dict:
        try:
            data = self._invoke(
                Quota.QUOTE, lambda: self._tsl.get_ohlc_data(names=[symbol]),
            )
            return dict(data.get(symbol, {}) or {})
        except RateLimited:
            raise
        except Exception:
            return {}

    def get_start_date(self):
        try:
            return self._invoke(Quota.NON_TRADING, lambda: self._tsl.get_start_date())
        except RateLimited:
            raise
        except Exception:
            return None

    def get_instrument_file(self):
        try:
            return self._invoke(
                Quota.NON_TRADING, lambda: self._tsl.get_instrument_file(),
            )
        except RateLimited:
            raise
        except Exception:
            return None

    @property
    def instrument_df(self) -> pd.DataFrame | None:
        try:
            return self._invoke(Quota.NON_TRADING, lambda: self._tsl.instrument_df)
        except RateLimited:
            raise
        except Exception:
            return None

    # ---- orders ------------------------------------------------------------

    def place_order(self, **kw) -> str:
        return self._invoke(
            Quota.ORDER, lambda: self._tsl.order_placement(**kw),
        )

    def place_super_order(self, **kw) -> str:
        return self._invoke(
            Quota.ORDER, lambda: self._tsl.place_super_order(**kw),
        )

    def cancel_order(self, order_id: str) -> None:
        self._invoke(Quota.ORDER, lambda: self._tsl.cancel_order(OrderID=order_id))

    def modify_order(self, order_id: str, **kw) -> None:
        self._invoke(
            Quota.ORDER, lambda: self._tsl.modify_order(order_id=order_id, **kw),
        )

    def get_order_status(self, order_id: str) -> str:
        return str(self._invoke(
            Quota.ORDER, lambda: self._tsl.get_order_status(orderid=order_id),
        )).upper()

    def get_order_detail(self, order_id: str) -> dict:
        try:
            raw = self._invoke(
                Quota.ORDER,
                lambda: self._tsl.get_order_detail(orderid=order_id, debug="NO") or {},
            )
        except RateLimited:
            raise
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

    def get_executed_price(self, order_id: str) -> float:
        try:
            return float(self._invoke(
                Quota.ORDER, lambda: self._tsl.get_executed_price(orderid=order_id),
            ))
        except RateLimited:
            raise
        except Exception:
            return 0.0

    def get_executed_price_and_time(self, order_id: str) -> tuple[float, str]:
        try:
            price, ts = self._invoke(
                Quota.ORDER,
                lambda: self._tsl.get_executed_price_and_time(orderid=order_id),
            )
            return float(price), str(ts)
        except RateLimited:
            raise
        except Exception:
            return 0.0, ""

    # ---- portfolio ---------------------------------------------------------

    def get_orderbook(self) -> list[dict]:
        return to_records(self._invoke(
            Quota.NON_TRADING, lambda: self._tsl.get_orderbook(debug="NO"),
        ))

    def get_trade_book(self) -> list[dict]:
        return to_records(self._invoke(
            Quota.NON_TRADING, lambda: self._tsl.get_trade_book(debug="NO"),
        ))

    def order_report(self):
        try:
            report = self._invoke(
                Quota.NON_TRADING, lambda: self._tsl.order_report(),
            )
        except RateLimited:
            raise
        except Exception:
            return {}
        if isinstance(report, dict):
            return {k: (to_records(v) if not isinstance(v, (str, int, float)) else v)
                    for k, v in report.items()}
        if isinstance(report, (tuple, list)):
            names = ("orders", "positions", "trades")
            return {names[i]: to_records(part) for i, part in enumerate(report)}
        return {}

    def get_live_pnl(self) -> float:
        try:
            return float(self._invoke(
                Quota.NON_TRADING, lambda: self._tsl.get_live_pnl() or 0.0,
            ))
        except RateLimited:
            raise
        except Exception:
            return 0.0

    def get_balance(self) -> float:
        return float(self._invoke(
            Quota.NON_TRADING, lambda: self._tsl.get_balance(),
        ))

    def get_positions(self) -> list:
        """Position domain objects — no pandas leaks past the transport
        boundary (M8). The raw DataFrame from Tradehull is normalised into
        ``Position`` objects exactly like the broker adapter path, so callers
        of the transport never see a DataFrame."""
        return DhanMapper.positions_from_df(self._invoke(
            Quota.NON_TRADING, lambda: self._tsl.get_positions(),
        ))

    def get_holdings(self) -> list:
        """Holding domain objects — no pandas leaks past the transport
        boundary (M8)."""
        return DhanMapper.holdings_from_df(self._invoke(
            Quota.NON_TRADING, lambda: self._tsl.get_holdings(),
        ))

    # ---- instrument metadata -----------------------------------------------

    def get_instrument_metadata(self, symbol: str, exchange: str,
                                underlying_symbol: str = "") -> dict:
        """Hydrate tick size / lot size / freeze qty from Dhan's instrument file."""
        try:
            idf = self.instrument_df
            if idf is None:
                return {}
            exch = DAY_BLOCK_MAPPED_EXCHANGE.get(exchange, exchange)
            df = idf[
                ((idf["SEM_TRADING_SYMBOL"] == symbol) | (idf["SEM_CUSTOM_SYMBOL"] == symbol))
                & (idf["SEM_EXM_EXCH_ID"] == exch)
            ]
            if df.empty and underlying_symbol:
                df = idf[
                    (idf["SM_SYMBOL_NAME"].astype(str) == underlying_symbol.upper())
                    & (idf["SEM_INSTRUMENT_NAME"] == "FUTCOM")
                ]
            if df.empty:
                return {}
            row = df.iloc[-1]
            tick = _first_float(row, "SEM_TICK_SIZE") or None
            lot = _first_int(row, "SEM_LOT_UNITS") or None
            frz = _first_int(row, "SEM_FREEZE_QTY") or None
            return {"tick_size": tick, "lot_size": lot, "freeze_qty": frz}
        except RateLimited:
            raise
        except Exception:
            return {}

    def blocks_day(self, symbol: str, exchange: str) -> bool:
        """True when Dhan's intraday wrapper rejects DAY for this instrument."""
        try:
            idf = self.instrument_df
            if idf is None:
                return exchange in ("MCX", "NFO", "BFO")
            exch = DAY_BLOCK_MAPPED_EXCHANGE.get(exchange, exchange)
            df = idf[
                ((idf["SEM_TRADING_SYMBOL"] == symbol) | (idf["SEM_CUSTOM_SYMBOL"] == symbol))
                & (idf["SEM_EXM_EXCH_ID"] == exch)
            ]
            if df.empty:
                return exchange in ("MCX", "NFO", "BFO")
            return "FUT" in str(df.iloc[-1]["SEM_INSTRUMENT_NAME"])
        except RateLimited:
            raise
        except Exception:
            return exchange in ("MCX", "NFO", "BFO")
