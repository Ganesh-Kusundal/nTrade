"""DhanMapper — pure mapping/normalization functions for Dhan broker data.

All methods are static and broker-agnostic: they transform raw Dhan API
responses (dicts, DataFrames) into ntrade domain objects (Quote, OrderBook,
Position, etc.).  No network calls, no authentication — pure data transforms.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from ntrade.domain.market.depth import DepthLevel, MarketDepth
from ntrade.domain.market.quote import Quote
from ntrade.domain.orders.book import OrderBook, OrderBookEntry, TradeBook, TradeBookEntry

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument


# Timeframe mapping: user-facing → Dhan interval string.
_DHAN_TIMEFRAMES = {
    "1m": "1", "2m": "2", "3m": "3", "4m": "4", "5m": "5",
    "15m": "15", "25m": "25", "60m": "60", "1h": "60",
    "day": "DAY", "1d": "DAY", "daily": "DAY",
}

# Mirrors Dhan-Tradehull's instrument_exchange mapping: NFO/BFO derivatives
# are filed under the cash exchange id, CUR under NSE.
DAY_BLOCK_MAPPED_EXCHANGE = {"NFO": "NSE", "BFO": "BSE", "CUR": "NSE"}


class DhanMapper:
    """Pure static mapping/normalization helpers for Dhan data."""

    # ---- symbol mapping ----------------------------------------------------

    @staticmethod
    def to_trading_symbol(instrument: "Instrument") -> str:
        """Map a domain instrument to a Dhan tradingsymbol string.

        Dhan's option symbols come in two formats:
          - SEM_TRADING_SYMBOL:   'NIFTY-Sep2026-29150-CE'   (hyphenated)
          - SEM_CUSTOM_SYMBOL:    'NIFTY 04 AUG 24400 CALL'  (spaced)
        The library's helpers (ATM/ITM/OTM, LTP, order placement) use the
        CUSTOM format, so we emit the spaced variant.
        """
        if instrument.KIND == "option":
            leg = "CALL" if instrument.option_type == "CE" else "PUT"
            date_part = instrument.expiry.strftime("%d %b").upper()
            strike_label = (
                int(instrument.strike)
                if instrument.strike == int(instrument.strike)
                else instrument.strike
            )
            return f"{instrument.underlying_symbol} {date_part} {strike_label} {leg}"
        return instrument.symbol

    # ---- timeframe ---------------------------------------------------------

    @staticmethod
    def map_timeframe(tf: str) -> str:
        """Map a user timeframe to Dhan's interval string, raising on unsupported."""
        key = str(tf).strip().lower()
        if key not in _DHAN_TIMEFRAMES:
            raise ValueError(
                f"Unsupported timeframe {tf!r}; Dhan supports "
                f"1m/2m/3m/4m/5m/15m/25m/60m/DAY (not 10m)"
            )
        return _DHAN_TIMEFRAMES[key]

    # ---- quote normalization -----------------------------------------------

    @staticmethod
    def normalize_quote(ltp: float, quote_data: dict | None = None, *, now: datetime | None = None) -> Quote:
        """Build a Quote from a raw LTP float and optional quote-data dict.

        ``now`` must be supplied by broker callers (kernel clock for replay
        parity); the wall-clock fallback exists only for legacy direct calls.
        """
        q = Quote(ltp=ltp, bid=ltp, ask=ltp, timestamp=now or datetime.now())
        if quote_data:
            q = q.with_update(
                high=_f(quote_data.get("high")),
                low=_f(quote_data.get("low")),
                open=_f(quote_data.get("open")),
                prev_close=_f(quote_data.get("close_price")),
                volume=int(quote_data.get("volume") or 0),
                oi=int(quote_data.get("open_interest") or 0),
            )
        return q

    # ---- history normalization ---------------------------------------------

    @staticmethod
    def normalize_history(df: pd.DataFrame | None) -> pd.DataFrame:
        """Normalize Dhan history columns to lowercase standard names."""
        empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df is None or df.empty:
            return empty
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        keep = [c for c in ("timestamp", "open", "high", "low", "close", "volume", "oi")
                if c in df.columns]
        return df[keep].reset_index(drop=True)

    @staticmethod
    def filter_history(
        df: pd.DataFrame,
        days: int | None = None,
        start: str | None = None,
        end: str | None = None,
        *,
        asof: datetime | None = None,
    ) -> pd.DataFrame:
        """Apply days/start/end filters to a normalized history frame.

        ``asof`` anchors the ``days`` cutoff (injected clock for replay
        determinism); it falls back to the wall clock when not supplied.
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

    # ---- order/trade book normalization ------------------------------------

    @staticmethod
    def normalize_orderbook(records: list[dict], *, now: datetime | None = None) -> OrderBook:
        """Normalize raw records into an OrderBook."""
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
        return OrderBook(entries=entries, timestamp=now or datetime.now())

    @staticmethod
    def normalize_tradebook(records: list[dict], *, now: datetime | None = None) -> TradeBook:
        """Normalize raw records into a TradeBook."""
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
        return TradeBook(entries=entries, timestamp=now or datetime.now())

    # ---- portfolio normalization -------------------------------------------

    @staticmethod
    def positions_from_df(df: pd.DataFrame | None) -> list:
        """Map a Dhan positions DataFrame into Position domain objects."""
        from ntrade.domain.portfolio import Position
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            symbol = _first_str(r, "tradingSymbol", "tradingsymbol")
            if not symbol:
                continue
            qty = _first_int(r, "netQty", "quantity")
            if qty == 0:
                qty = _first_int(r, "buyQty") - _first_int(r, "sellQty")
            out.append(Position(
                symbol=symbol, quantity=qty,
                avg_price=_first_float(r, "avgTradingPrice", "avgPrice"),
                ltp=_first_float(r, "ltp"),
                product=_first_str(r, "productType") or "MIS",
                exchange=_first_str(r, "exchangeSegment") or "NSE",
            ))
        return out

    @staticmethod
    def holdings_from_df(df: pd.DataFrame | None) -> list:
        """Map a Dhan holdings DataFrame into Holding domain objects."""
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

    # ---- depth normalization -----------------------------------------------

    @staticmethod
    def normalize_depth(
        symbol: str,
        bid_df: pd.DataFrame,
        ask_df: pd.DataFrame, *, now: datetime | None = None,
    ) -> MarketDepth:
        """Build a MarketDepth from Dhan's depth DataFrames."""
        bids = tuple(
            DepthLevel(
                price=_f(r.get("bid_price") or r.get("price")),
                quantity=int(r.get("bid_qty") or r.get("quantity") or 0),
            )
            for _, r in bid_df.iterrows()
        )
        asks = tuple(
            DepthLevel(
                price=_f(r.get("ask_price") or r.get("price")),
                quantity=int(r.get("ask_qty") or r.get("quantity") or 0),
            )
            for _, r in ask_df.iterrows()
        )
        return MarketDepth(symbol=symbol, bids=bids, asks=asks, timestamp=now or datetime.now())


# ---- record normalization --------------------------------------------------

def to_records(value) -> list[dict]:
    """Normalize a DataFrame / dict / list into a list of row dicts.

    Dhan's order/trade book endpoints return DataFrames (or dicts), which are
    ambiguous for truthiness and unhelpful as raw returns.  Symbol-keyed dicts
    keep their key merged into each row as ``symbol``.
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


# ---- option chain builder --------------------------------------------------

def chain_from_dhan_df(underlying, df: pd.DataFrame, atm: float,
                       expiry: date | None = None,
                       asof: datetime | None = None) -> "OptionChain":
    """Build an OptionChain from a Dhan-style chain dataframe."""
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


# ---- scalar helpers --------------------------------------------------------

def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


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
