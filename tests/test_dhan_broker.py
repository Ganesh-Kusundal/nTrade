"""Unit tests for DhanBroker with a stubbed Tradehull (no live network)."""

import time
import types
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ntrade.brokers.dhan import DhanBroker, dhan_symbol
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.domain.instruments.cash import Equity, Index
from ntrade.domain.instruments.derivatives import Option
from ntrade.domain.orders.order import Order, OrderSide, OrderType, TradeType


def make_broker(**tsl_methods):
    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(**tsl_methods)
    broker._transport = DhanTransport(broker.tsl)
    return broker


def test_dhan_symbol_mapping():
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400,
                 expiry=date(2026, 8, 1), option_type="CE", underlying_symbol="NIFTY")
    assert "NIFTY" in dhan_symbol(opt)
    assert "24400" in dhan_symbol(opt)
    assert "CALL" in dhan_symbol(opt)
    assert dhan_symbol(Equity("RELIANCE")) == "RELIANCE"


def test_dhan_symbol_option_custom_format():
    """Options must map to Dhan's SEM_CUSTOM_SYMBOL format (no year, UPPER month)
    so get_lot_size / LTP / order placement match the instrument file."""
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400,
                 expiry=date(2026, 8, 4), option_type="CE", underlying_symbol="NIFTY")
    assert dhan_symbol(opt) == "NIFTY 04 AUG 24400 CALL"
    put = Option("NIFTY 24400 PE", exchange="NFO", strike=24400,
                 expiry=date(2026, 8, 4), option_type="PE", underlying_symbol="NIFTY")
    assert dhan_symbol(put) == "NIFTY 04 AUG 24400 PUT"


def test_dhan_symbol_option_fractional_strike():
    """Currency/commodity options keep fractional strikes (USDINR 83.2)."""
    from ntrade.domain.instruments.derivatives import Option as _Option
    opt = _Option("USDINR 83.2 CE", exchange="NSE", strike=83.2,
                  expiry=date(2026, 9, 26), option_type="CE", underlying_symbol="USDINR")
    assert dhan_symbol(opt) == "USDINR 26 SEP 83.2 CALL"


def test_get_expiry_date_returns_list():
    """Dhan's get_expiry_date returns a LIST of expiry dates; our adapter must
    normalize to list[date] (not crash on pd.to_datetime of a list)."""
    broker = make_broker(get_expiry_date=lambda **kw: ["2026-08-04", "2026-08-11"])
    nifty = Index("NIFTY")
    dates = broker.get_expiry_date(nifty, "OPTION")
    assert dates == [date(2026, 8, 4), date(2026, 8, 11)]


def test_get_expiry_date_empty_on_failure():
    broker = make_broker(get_expiry_date=lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="expiry date"):
        broker.get_expiry_date(Index("NIFTY"), "OPTION")


def test_expiry_list_raises_on_transport_failure():
    broker = make_broker(get_expiry_list=lambda **kw: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(RuntimeError, match="expiry list"):
        broker.get_expiry_list(Index("NIFTY"))


def test_expiry_list_still_propagates_rate_limited():
    from ntrade.execution.rate_limit import RateLimited, Quota
    broker = make_broker(get_expiry_list=lambda **kw: (_ for _ in ()).throw(RateLimited(Quota.NON_TRADING)))
    with pytest.raises(RateLimited):
        broker.get_expiry_list(Index("NIFTY"))


def test_get_quote_no_duplicate_kwargs():
    """Regression: get_quote must not pass bid/ask twice to Quote()."""
    broker = make_broker(
        get_ltp_data=lambda names: {names[0]: 24383.6},
        get_quote_data=lambda names: {names[0]: {"high": 24500, "low": 24200, "open": 24300,
                                                "close_price": 24350, "volume": 1000,
                                                "open_interest": 500}},
    )
    nifty = Index("NIFTY")
    quote = broker.get_quote(nifty)
    assert quote.ltp == 24383.6
    assert quote.bid == 24383.6
    assert quote.ask == 24383.6
    assert quote.high == 24500.0
    assert quote.volume == 1000
    assert quote.oi == 500
    assert quote.prev_close == 24350.0


def test_get_quote_raises_on_failure():
    broker = make_broker(get_ltp_data=lambda names: (_ for _ in ()).throw(RuntimeError("boom")))
    nifty = Index("NIFTY")
    with pytest.raises(RuntimeError, match="LTP fetch failed"):
        broker.get_quote(nifty)


def test_get_ltp_rejects_failure_envelope():
    """Regression: the Tradehull SDK prints "Exception at calling ltp as
    {'status': 'failure', ...}" and returns a failure envelope instead of
    raising. get_ltp must treat that envelope as a hard failure, never as a
    valid LTP — otherwise a dead/blocked fetch surfaces a stale or seed value
    as a real price (the bug behind a silent 24565.45 in production)."""
    failure_env = {
        "status": "failure",
        "remarks": {"error_code": None, "error_type": None, "error_message": None},
        "data": "",
    }
    broker = make_broker(get_ltp_data=lambda names: failure_env)
    nifty = Index("NIFTY")
    with pytest.raises(RuntimeError, match="LTP fetch failed"):
        broker.get_quote(nifty)


def test_dhan_timeframe_mapping():
    from ntrade.brokers.dhan_mapper import DhanMapper
    assert DhanMapper.map_timeframe("5m") == "5"
    assert DhanMapper.map_timeframe("1m") == "1"
    assert DhanMapper.map_timeframe("15m") == "15"
    assert DhanMapper.map_timeframe("25m") == "25"
    assert DhanMapper.map_timeframe("60m") == "60"
    assert DhanMapper.map_timeframe("1h") == "60"
    assert DhanMapper.map_timeframe("1d") == "DAY"
    assert DhanMapper.map_timeframe("DAY") == "DAY"


def test_dhan_timeframe_rejects_10m():
    """Dhan has NO 10-minute interval — must raise, not silently fall back to 5."""
    from ntrade.brokers.dhan_mapper import DhanMapper
    with pytest.raises(ValueError):
        DhanMapper.map_timeframe("10m")
    with pytest.raises(ValueError):
        DhanMapper.map_timeframe("daily-x")


def test_dhan_timeframe_sub5m_maps_to_1m_base():
    """B-013/F-001: 2m/3m/4m are NOT native Dhan intervals — they map to the
    1m base interval so the transport fetches 1m and resamples, instead of
    sending an unsupported "2"/"3"/"4" to the backend (which used to fail
    and silently return an empty CandleSeries)."""
    from ntrade.brokers.dhan_mapper import DhanMapper
    assert DhanMapper.map_timeframe("2m") == "1"
    assert DhanMapper.map_timeframe("3m") == "1"
    assert DhanMapper.map_timeframe("4m") == "1"
    assert DhanMapper.resample_rule("2m") == "2min"
    assert DhanMapper.resample_rule("3m") == "3min"
    assert DhanMapper.resample_rule("4m") == "4min"
    assert DhanMapper.resample_rule("5m") is None  # native, no resample


def test_get_historical_rejects_unsupported_timeframe():
    broker = make_broker(get_historical_data=lambda **kw: pd.DataFrame())
    rel = Equity("RELIANCE")
    with pytest.raises(ValueError):
        broker.get_historical(rel, timeframe="10m")


def test_get_historical_day_routes_to_daily_endpoint_for_commodity():
    """Dhan's intraday wrapper client-side blocks DAY for FUT contracts, but the
    daily historical endpoint serves them for ALL segments incl. MCX (verified
    against the official docs). get_historical must route DAY on commodity/
    future exchanges through get_long_term_historical_data."""
    from ntrade.domain.instruments.cash import Commodity
    intraday_hits = []
    day_df = pd.DataFrame({
        "Timestamp": ["2026-07-01 00:00:00", "2026-07-02 00:00:00"],
        "Open": [100, 101], "High": [102, 103], "Low": [99, 100],
        "Close": [101, 102], "Volume": [1000, 2000],
    })
    broker = make_broker(
        get_historical_data=lambda **kw: intraday_hits.append(1) or pd.DataFrame(),
        get_long_term_historical_data=lambda **kw: day_df,
    )
    gold = Commodity("GOLD")
    df = broker.get_historical(gold, timeframe="1d")
    assert len(df) == 2
    assert intraday_hits == []  # intraday wrapper must NOT be hit for MCX DAY


def test_get_historical_day_nfo_index_option_uses_intraday_wrapper():
    """NFO index options (OPTIDX) are NOT blocked by the wrapper for DAY — they
    must keep the intraday path (routing them to the daily endpoint's NFO→NSE
    lookup would return empty). Mirrors the wrapper's 'FUT' instrument-type
    check via the instrument file."""
    # Dhan's instrument file files NFO index derivatives under the cash
    # exchange id (SEM_EXM_EXCH_ID='NSE') — mirror the wrapper's NFO→NSE map.
    idf = pd.DataFrame([{
        "SEM_TRADING_SYMBOL": "NIFTY-Sep2026-24400-CE",
        "SEM_CUSTOM_SYMBOL": "NIFTY 04 SEP 24400 CALL",
        "SEM_EXM_EXCH_ID": "NSE",
        "SEM_INSTRUMENT_NAME": "OPTIDX",
    }])
    hits = []
    day_df = pd.DataFrame({
        "Timestamp": ["2026-07-01 00:00:00"], "Open": [1], "High": [2],
        "Low": [0.5], "Close": [1.5], "Volume": [10],
    })
    broker = make_broker(
        instrument_df=idf,
        get_historical_data=lambda **kw: hits.append("intraday") or day_df,
        get_long_term_historical_data=lambda **kw: hits.append("daily") or pd.DataFrame(),
    )
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400,
                 expiry=date(2026, 9, 4), option_type="CE", underlying_symbol="NIFTY")
    df = broker.get_historical(opt, timeframe="1d")
    assert hits == ["intraday"]
    assert len(df) == 1


def test_get_historical_day_fallback_empty_is_benign():
    """If the daily endpoint returns nothing for the range (e.g. holiday), DAY on
    a commodity returns an empty frame rather than raising."""
    from ntrade.domain.instruments.cash import Commodity
    broker = make_broker(get_long_term_historical_data=lambda **kw: pd.DataFrame())
    assert broker.get_historical(Commodity("GOLD"), timeframe="1d").empty


def test_get_historical_day_ok_for_equity_empty():
    """An empty DAY frame for NSE equity is NOT an error (e.g. weekend, new
    listing) — it goes through the intraday wrapper as usual."""
    broker = make_broker(get_historical_data=lambda **kw: pd.DataFrame())
    assert broker.get_historical(Equity("RELIANCE"), timeframe="1d").empty


def test_get_historical_normalizes_columns():
    raw = pd.DataFrame({
        "Timestamp": ["2026-08-01 09:15:00", "2026-08-01 09:20:00"],
        "Open": [100, 101], "High": [102, 103], "Low": [99, 100],
        "Close": [101, 102], "Volume": [1000, 2000],
    })
    broker = make_broker(get_historical_data=lambda **kw: raw)
    nifty = Index("NIFTY")
    df = broker.get_historical(nifty, timeframe="5m")
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(df) == 2


def test_get_depth_index_returns_none():
    broker = make_broker()
    assert broker.get_depth(Index("NIFTY")) is None


def test_get_depth_uses_dhan_symbol_for_option_short_label():
    """Chain ATM labels like 'NIFTY 24600 CE' must be mapped to CALL form
    before depth subscribe — otherwise Tradehull returns None silently."""
    seen = {}

    def capture_subscribe(pairs):
        seen["pairs"] = pairs
        return {"NIFTY 04 AUG 24600 CALL|NFO": "client"}

    broker = make_broker(
        full_market_depth_data=capture_subscribe,
        get_market_depth_df=lambda dc: (
            pd.DataFrame([{"bid_price": 50.0, "bid_qty": 100}]),
            pd.DataFrame([{"ask_price": 50.5, "ask_qty": 80}]),
        ),
    )
    opt = Option("NIFTY 24600 CE", exchange="NFO", strike=24600,
                 expiry=date(2026, 8, 4), option_type="CE", underlying_symbol="NIFTY")
    depth = broker.get_depth(opt, settle=0.0)
    assert seen["pairs"] == [("NIFTY 04 AUG 24600 CALL", "NFO")]
    assert depth is not None
    assert depth.best_bid().price == 50.0


def test_get_depth_nse_works():
    """Dhan's full_market_depth_data returns an OrderedDict keyed 'SYM|EXCH';
    get_market_depth_df consumes the individual client and yields bid_price/
    bid_qty columns (also tolerant of plain price/quantity frames)."""
    broker = make_broker(
        full_market_depth_data=lambda *a, **kw: {"RELIANCE|NSE": "client"},
        get_market_depth_df=lambda dc: (
            pd.DataFrame([{"bid_price": 100.0, "bid_qty": 500}]),
            pd.DataFrame([{"ask_price": 100.5, "ask_qty": 400}]),
        ),
    )
    rel = Equity("RELIANCE")
    depth = broker.get_depth(rel)
    assert depth is not None
    assert depth.best_bid().price == 100.0
    assert depth.best_ask().price == 100.5


def test_get_depth_legacy_columns_fallback():
    """Tolerate frames with plain price/quantity columns (old/other brokers)."""
    broker = make_broker(
        full_market_depth_data=lambda *a, **kw: {"TCS|NSE": "client"},
        get_market_depth_df=lambda dc: (
            pd.DataFrame([{"price": 10.0, "quantity": 100}]),
            pd.DataFrame([{"price": 10.5, "quantity": 200}]),
        ),
    )
    depth = broker.get_depth(Equity("TCS"))
    assert depth is not None
    assert depth.best_bid().price == 10.0


def test_get_depth_empty_frames_return_none():
    """Genuinely empty frames (e.g. illiquid / pre-open) resolve to None after
    the retry budget is exhausted — settle=0 keeps this unit test fast."""
    broker = make_broker(
        full_market_depth_data=lambda *a, **kw: {"RELIANCE|NSE": "client"},
        get_market_depth_df=lambda dc: (pd.DataFrame(), pd.DataFrame()),
    )
    assert broker.get_depth(Equity("RELIANCE"), settle=0.0) is None


def test_get_depth_missing_client_falls_back():
    """If the exact SYM|EXCH key is absent, fall back to the first client."""
    broker = make_broker(
        full_market_depth_data=lambda *a, **kw: {"RELIANCE|NSE": "client"},
        get_market_depth_df=lambda dc: (
            pd.DataFrame([{"bid_price": 1.0, "bid_qty": 1}]),
            pd.DataFrame([{"ask_price": 2.0, "ask_qty": 1}]),
        ),
    )
    depth = broker.get_depth(Equity("RELIANCE", exchange="NSE"))
    assert depth is not None


def test_get_depth_retries_empty_snapshot_then_succeeds():
    """A first snapshot returning no depth client is retried (fresh websocket
    subscription re-arms the stream) — the second attempt succeeds."""
    calls = {"n": 0}

    def flaky_subscribe(*a, **kw):
        calls["n"] += 1
        return {} if calls["n"] == 1 else {"RELIANCE|NSE": "client"}

    broker = make_broker(
        full_market_depth_data=flaky_subscribe,
        get_market_depth_df=lambda dc: (
            pd.DataFrame([{"bid_price": 100.0, "bid_qty": 500}]),
            pd.DataFrame([{"ask_price": 100.5, "ask_qty": 400}]),
        ),
    )
    depth = broker.get_depth(Equity("RELIANCE"), settle=0.0)
    assert depth is not None
    assert depth.best_bid().price == 100.0
    assert calls["n"] == 2


def test_get_depth_retries_on_frame_timeout_then_succeeds():
    """A hung frame read (websocket snapshot timeout) is retried — the next
    attempt gets the frames. Uses a real short timeout, no live network."""
    calls = {"n": 0}

    def slow_first_frame(dc):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.2)   # exceeds the 0.05s bounded timeout
        return (
            pd.DataFrame([{"bid_price": 10.0, "bid_qty": 100}]),
            pd.DataFrame([{"ask_price": 10.5, "ask_qty": 200}]),
        )

    broker = make_broker(
        full_market_depth_data=lambda *a, **kw: {"RELIANCE|NSE": "client"},
        get_market_depth_df=slow_first_frame,
    )
    depth = broker.get_depth(Equity("RELIANCE"), timeout=0.05, settle=0.0)
    assert depth is not None
    assert depth.best_bid().price == 10.0
    assert calls["n"] == 2


def test_get_depth_rate_limited_not_retried():
    """A DH-904 on the depth subscription must propagate immediately (B-011
    no-amplify) — no retry loop that burns more DATA quota."""
    from ntrade.execution.rate_limit import Quota, RateLimited
    calls = {"n": 0}

    def rate_limited(*a, **kw):
        calls["n"] += 1
        raise RateLimited(Quota.DATA)

    broker = make_broker(full_market_depth_data=rate_limited)
    with pytest.raises(RateLimited):
        broker.get_depth(Equity("RELIANCE"), attempts=3)
    assert calls["n"] == 1


def test_get_depth_timeout_rate_limited_not_swallowed():
    """A DH-904 raised INSIDE the frame read must surface as RateLimited, not
    be swallowed as a timed-out None (never mask quota exhaustion)."""
    from ntrade.execution.rate_limit import Quota, RateLimited

    def rl_frame(dc):
        raise RateLimited(Quota.DATA)

    broker = make_broker(
        full_market_depth_data=lambda *a, **kw: {"RELIANCE|NSE": "client"},
        get_market_depth_df=rl_frame,
    )
    with pytest.raises(RateLimited):
        broker.get_depth(Equity("RELIANCE"), timeout=0.05, attempts=1)


def test_get_quote_retries_flaky_ltp():
    """Dhan's get_ltp_data intermittently returns None — get_quote must retry."""
    attempts = {"n": 0}

    def flaky_ltp(names):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return {names[0]: None}  # transient failure
        return {names[0]: 24383.6}

    broker = make_broker(get_ltp_data=flaky_ltp, get_quote_data=lambda names: {})
    nifty = Index("NIFTY")
    quote = broker.get_quote(nifty)
    assert quote.ltp == 24383.6
    assert attempts["n"] == 3


def test_get_option_chain_falls_back_on_none():
    """Dhan may return None for a given expiry (its LTP call fails transiently).
    The adapter must try the next expiry instead of crashing on unpacking."""
    rows = []
    for strike in (24400, 24450):
        for leg in ("CE", "PE"):
            rows.append({
                "Strike Price": strike,
                f"{leg} LTP": 50.0, f"{leg} OI": 10000, f"{leg} Volume": 100,
                f"{leg} IV": 0.15, f"{leg} Delta": 0.5, f"{leg} Gamma": 0.001,
                f"{leg} Theta": -0.2, f"{leg} Vega": 0.1,
            })
    chain_df = pd.DataFrame(rows)
    calls = []

    def flaky_chain(**kw):
        calls.append(kw["expiry"])
        if kw["expiry"] == 0:
            return None  # transient failure on requested expiry
        return (24450, chain_df)

    broker = make_broker(get_option_chain=flaky_chain)
    nifty = Index("NIFTY")
    chain = broker.get_option_chain(nifty, expiry=0, num_strikes=2)
    assert len(chain) == 4
    assert calls == [0, 1]  # 0 failed, fell back to 1


def test_get_option_chain_raises_then_recovers():
    """The library can RAISE (not just return None) on a bad expiry — the
    adapter must catch that and fall back to the next expiry."""
    rows = []
    for strike in (24400, 24450):
        for leg in ("CE", "PE"):
            rows.append({
                "Strike Price": strike,
                f"{leg} LTP": 50.0, f"{leg} OI": 10000, f"{leg} Volume": 100,
                f"{leg} IV": 0.15, f"{leg} Delta": 0.5, f"{leg} Gamma": 0.001,
                f"{leg} Theta": -0.2, f"{leg} Vega": 0.1,
            })
    chain_df = pd.DataFrame(rows)
    calls = []

    def raisy_chain(**kw):
        calls.append(kw["expiry"])
        if kw["expiry"] == 0:
            raise RuntimeError("transient failure")
        return (24450, chain_df)

    broker = make_broker(get_option_chain=raisy_chain)
    nifty = Index("NIFTY")
    chain = broker.get_option_chain(nifty, expiry=0, num_strikes=2)
    assert len(chain) == 4
    assert calls == [0, 1]
    assert chain.expiry_index_used == 1


def test_get_option_chain_rejects_empty_frame():
    """An empty chain frame counts as failure and triggers the fallback."""
    broker = make_broker(get_option_chain=lambda **kw: (24450, pd.DataFrame()))
    nifty = Index("NIFTY")
    with pytest.raises(RuntimeError):
        broker.get_option_chain(nifty, expiry=0, num_strikes=2)


def test_get_option_chain_raises_when_all_fail():
    broker = make_broker(get_option_chain=lambda **kw: None)
    nifty = Index("NIFTY")
    with pytest.raises(RuntimeError):
        broker.get_option_chain(nifty, expiry=0, num_strikes=2)


def test_get_option_chain_builds_options():
    rows = []
    for strike in (24400, 24450, 24500):
        for leg in ("CE", "PE"):
            rows.append({
                "Strike Price": strike,
                f"{leg} LTP": 50.0, f"{leg} OI": 10000, f"{leg} Volume": 100,
                f"{leg} IV": 0.15, f"{leg} Delta": 0.5, f"{leg} Gamma": 0.001,
                f"{leg} Theta": -0.2, f"{leg} Vega": 0.1,
            })
    chain_df = pd.DataFrame(rows)
    broker = make_broker(get_option_chain=lambda **kw: (24450, chain_df))
    nifty = Index("NIFTY")
    chain = broker.get_option_chain(nifty, expiry=0, num_strikes=3)
    assert len(chain) == 6
    assert chain.atm_strike == 24450.0
    assert chain.atm.strike == 24450.0
    assert chain.atm.greeks.delta == 0.5
    assert chain.atm.iv == 0.15


def test_place_order_passes_dhan_params():
    broker = make_broker(order_placement=lambda **kw: "ORD-42")
    rel = Equity("RELIANCE")
    rel._quote = rel._quote.with_update(ltp=2500.0)
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=10,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=2500.0)
    broker.place_order(order)
    assert order.order_id == "ORD-42"
    assert order.status.value == "PENDING"


def test_place_order_rejection_propagates():
    broker = make_broker(order_placement=lambda **kw: (_ for _ in ()).throw(RuntimeError("rejected")))
    rel = Equity("RELIANCE")
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=10,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=10.0)
    with pytest.raises(RuntimeError):
        broker.place_order(order)
    assert order.status.value == "REJECTED"


# ------------------------------------------------------------- SEBI (H-2)
def _fno_market_order(side=OrderSide.BUY):
    from ntrade.domain.instruments.derivatives import Future
    fut = Future("NIFTY FUT")  # exchange NFO -> SEBI conversion applies
    order = Order(instrument=fut, side=side, quantity=25,
                  order_type=OrderType.MARKET, trade_type=TradeType.MIS)
    return fut, order


def test_sebi_market_to_limit_fresh_quote_default_band():
    from datetime import datetime
    broker = make_broker(order_placement=lambda **kw: "ORD-77")
    fut, order = _fno_market_order(OrderSide.BUY)
    fut._quote = fut._quote.with_update(ltp=24500.0, timestamp=datetime.now())
    broker.place_order(order)
    assert order.order_type == OrderType.LIMIT
    assert order.price == round(24500.0 * 1.02, 1)
    fut2, sell = _fno_market_order(OrderSide.SELL)
    fut2._quote = fut2._quote.with_update(ltp=24500.0, timestamp=datetime.now())
    broker.place_order(sell)
    assert sell.price == round(24500.0 * 0.98, 1)


def test_sebi_market_to_limit_rejects_stale_quote():
    from datetime import datetime, timedelta
    broker = make_broker(order_placement=lambda **kw: "ORD-78")
    fut, order = _fno_market_order()
    fut._quote = fut._quote.with_update(
        ltp=24500.0, timestamp=datetime.now() - timedelta(seconds=60))
    with pytest.raises(RuntimeError, match="max age"):
        broker.place_order(order)


def test_sebi_market_to_limit_rejects_untimestamped_quote():
    broker = make_broker(order_placement=lambda **kw: "ORD-79")
    fut, order = _fno_market_order()
    fut._quote = fut._quote.with_update(ltp=24500.0)  # no timestamp -> stale
    with pytest.raises(RuntimeError, match="untimestamped"):
        broker.place_order(order)


def test_sebi_band_is_configurable():
    from datetime import datetime
    broker = make_broker(order_placement=lambda **kw: "ORD-80")
    broker._sebi_band_pct = 1.0
    fut, order = _fno_market_order()
    fut._quote = fut._quote.with_update(ltp=24500.0, timestamp=datetime.now())
    broker.place_order(order)
    assert order.price == round(24500.0 * 1.01, 1)


def test_depth20_capability_only_for_dhan():
    from ntrade.brokers.capabilities import registered_capabilities
    cap = registered_capabilities()["depth20"]
    assert cap.supports("dhan")
    assert not cap.supports("paper")


def test_historical_days_filter_applied():
    # Data spans several days (anchored to now) so the relative days=1 cutoff
    # deterministically trims rows regardless of when the test runs.
    now = pd.Timestamp.now()
    raw = pd.DataFrame({
        "Timestamp": pd.date_range(end=now, periods=720, freq="5min"),  # ~2.5 days
        "Open": [100] * 720, "High": [102] * 720, "Low": [99] * 720,
        "Close": [101] * 720, "Volume": [1000] * 720,
    })
    broker = make_broker(get_historical_data=lambda **kw: raw)
    nifty = Index("NIFTY")
    # days=10000 covers everything; days=1 keeps only the last day of data.
    all_df = broker.get_historical(nifty, timeframe="5m", days=10000)
    short_df = broker.get_historical(nifty, timeframe="5m", days=1)
    assert len(all_df) == 720
    assert len(short_df) < len(all_df)
    assert len(short_df) >= 100  # the last day is a meaningful subset


def test_positions_normalized_to_domain_objects():
    df = pd.DataFrame([
        {"tradingSymbol": "RELIANCE", "netQty": 10, "avgTradingPrice": 100.0, "ltp": 110.0, "productType": "MIS", "exchangeSegment": "NSE"},
        {"tradingSymbol": "TCS", "netQty": -5, "avgTradingPrice": 50.0, "ltp": 45.0, "productType": "CNC", "exchangeSegment": "NSE"},
    ])
    broker = make_broker(get_positions=lambda: df, get_holdings=lambda: df)
    positions = broker.get_positions()
    assert len(positions) == 2
    assert positions[0].symbol == "RELIANCE"
    assert positions[0].pnl == 100.0
    holdings = broker.get_holdings()
    assert holdings[0].symbol == "RELIANCE"


def test_positions_empty_dataframe():
    broker = make_broker(get_positions=lambda: pd.DataFrame(), get_holdings=lambda: pd.DataFrame())
    assert broker.get_positions() == []
    assert broker.get_holdings() == []


def test_positions_nan_safe():
    """Dhan frames carry NaN for missing numerics — must not crash or emit 'nan' symbols."""
    import numpy as np
    df = pd.DataFrame([
        {"tradingSymbol": np.nan, "netQty": np.nan},  # bad row -> skipped
        {"tradingSymbol": "RELIANCE", "netQty": 10, "avgTradingPrice": 100.0, "ltp": 110.0},
    ])
    broker = make_broker(get_positions=lambda: df, get_holdings=lambda: df)
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "RELIANCE"
    assert positions[0].quantity == 10
    holdings = broker.get_holdings()
    assert len(holdings) == 1
    assert holdings[0].symbol == "RELIANCE"


def test_historical_days_filter_tz_aware():
    """Dhan returns tz-aware (IST) timestamps — days cutoff must not crash."""
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    raw = pd.DataFrame({
        "Timestamp": pd.date_range(end=now, periods=720, freq="5min"),
        "Open": [100] * 720, "High": [102] * 720, "Low": [99] * 720,
        "Close": [101] * 720, "Volume": [1000] * 720,
    })
    broker = make_broker(get_historical_data=lambda **kw: raw)
    nifty = Index("NIFTY")
    all_df = broker.get_historical(nifty, timeframe="5m", days=10000)
    short_df = broker.get_historical(nifty, timeframe="5m", days=1)
    assert len(all_df) == 720
    assert len(short_df) < len(all_df)


def test_position_quantity_buy_sell_fallback():
    """Regression: rows without netQty must derive quantity as buyQty - sellQty."""
    df = pd.DataFrame([
        {"tradingSymbol": "TCS", "buyQty": 10, "sellQty": 5},
        {"tradingSymbol": "INFY", "netQty": 3},
    ])
    broker = make_broker(get_positions=lambda: df)
    positions = broker.get_positions()
    by_symbol = {p.symbol: p.quantity for p in positions}
    assert by_symbol["TCS"] == 5   # 10 - 5, the restored fallback
    assert by_symbol["INFY"] == 3  # explicit netQty wins


def test_no_fake_streaming_capabilities():
    import ntrade.brokers.dhan as dhan_mod
    from ntrade.brokers.capabilities import registered_capabilities

    assert not hasattr(dhan_mod, "_market_feed")
    assert not hasattr(dhan_mod, "_order_update_stream")
    caps = registered_capabilities()
    assert "market_feed" not in caps
    assert "order_update_stream" not in caps


def test_broker_stop_cancels_auth_timer():
    from unittest.mock import Mock

    broker = DhanBroker(connect=False)
    broker._auth = Mock()
    broker.stop()
    broker._auth.stop.assert_called_once()


# ------------------------------------------------------------- B-010


def test_connect_uses_broker_rate_gate():
    """connect() builds a shared BrokerRateGate (not the old 10/s RateLimiter)."""
    from ntrade.brokers.dhan import DhanBroker
    from ntrade.execution.rate_limit import BrokerRateGate
    broker = DhanBroker(connect=False)
    broker._auth = MagicMock()
    broker._auth.authenticate.return_value = object()
    broker.connect()
    assert isinstance(broker._gate, BrokerRateGate)
    assert broker._transport._gate is broker._gate
    assert not hasattr(broker, "_rate_limiter")


def test_place_order_routes_through_transport():
    """Order placement goes through the throttled transport, never self.tsl."""
    broker = make_broker(order_placement=lambda **kw: "O1")
    broker._transport = MagicMock()
    broker._transport.place_order.return_value = "O1"
    rel = Equity("RELIANCE")
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=10,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=2500.0)
    broker.place_order(order)
    broker._transport.place_order.assert_called_once()
    assert order.order_id == "O1"


def test_cancel_routes_through_transport():
    broker = make_broker()
    broker._transport = MagicMock()
    rel = Equity("RELIANCE")
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=1,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS, price=10.0,
                  order_id="ORD-1")
    broker.cancel_order(order)
    broker._transport.cancel_order.assert_called_once_with("ORD-1")
    assert order.status.value == "CANCELLED"


def test_get_order_status_routes_through_transport():
    broker = make_broker()
    broker._transport = MagicMock()
    broker._transport.get_order_status.return_value = "COMPLETE"
    rel = Equity("RELIANCE")
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=1,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS,
                  price=10.0, order_id="ORD-2")
    out = broker.get_order_status(order)
    broker._transport.get_order_status.assert_called_once_with("ORD-2")
    assert out.status.value == "COMPLETED"


def test_order_status_rate_limited_not_swallowed():
    """A DH-904 on order-status polling must propagate, not return a stale order."""
    from ntrade.execution.rate_limit import Quota, RateLimited
    broker = make_broker()
    broker._transport = MagicMock()
    broker._transport.get_order_status.side_effect = RateLimited(Quota.ORDER)
    rel = Equity("RELIANCE")
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=1,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS,
                  price=10.0, order_id="ORD-3")
    with pytest.raises(RateLimited):
        broker.get_order_status(order)


def test_order_status_transport_error_raises_not_silent_stale():
    """H-5: a transport failure on the status refresh must raise, not silently
    return the stale order — the caller's staleness accounting depends on the
    error surfacing (BrokerExecution counts it and evicts after _stale_limit)."""
    broker = make_broker()
    broker._transport = MagicMock()
    broker._transport.get_order_status.side_effect = RuntimeError("connection dead")
    rel = Equity("RELIANCE")
    order = Order(instrument=rel, side=OrderSide.BUY, quantity=1,
                  order_type=OrderType.LIMIT, trade_type=TradeType.MIS,
                  price=10.0, order_id="ORD-4")
    with pytest.raises(RuntimeError, match="refresh failed"):
        broker.get_order_status(order)


def test_option_chain_rate_limited_does_not_retry_expiries():
    """A DH-904 must not trigger the 3-expiry fallback loop (no quota amplification)."""
    from ntrade.execution.rate_limit import Quota, RateLimited
    broker = make_broker()
    broker._transport = MagicMock()
    broker._transport.get_option_chain.side_effect = RateLimited(Quota.DATA)
    nifty = Index("NIFTY")
    with pytest.raises(RateLimited):
        broker.get_option_chain(nifty, expiry=0, num_strikes=2)
    broker._transport.get_option_chain.assert_called_once()


# ------------------------------------------------------------- K-021


def test_expiry_list_propagates_rate_limited():
    """A DH-904 on get_expiry_list must raise RateLimited, never return []
    (an empty expiry list looks like 'no expiries' to strategies, ERROR-016)."""
    from ntrade.execution.rate_limit import RateLimited
    broker = make_broker(get_expiry_list=lambda **kw: (_ for _ in ()).throw(RuntimeError("DH-904")))
    nifty = Index("NIFTY")
    with pytest.raises(RateLimited):
        broker.get_expiry_list(nifty)


def test_orderbook_propagates_rate_limited():
    """A DH-904 on get_orderbook must raise RateLimited, never return an empty
    OrderBook (an empty book looks like 'no orders')."""
    from ntrade.execution.rate_limit import RateLimited
    broker = make_broker(get_orderbook=lambda debug="NO": (_ for _ in ()).throw(RuntimeError("Rate_Limit")))
    with pytest.raises(RateLimited):
        broker.get_orderbook()


def test_trade_book_propagates_rate_limited():
    from ntrade.execution.rate_limit import RateLimited
    broker = make_broker(get_trade_book=lambda debug="NO": (_ for _ in ()).throw(RuntimeError("DH-904")))
    with pytest.raises(RateLimited):
        broker.get_trade_book()


def test_lot_size_propagates_rate_limited():
    """A DH-904 on get_lot_size must raise RateLimited, never return 0 (a 0 lot
    size can divide-by-zero downstream)."""
    from ntrade.execution.rate_limit import RateLimited
    broker = make_broker(get_lot_size=lambda tradingsymbol: (_ for _ in ()).throw(RuntimeError("DH-904")))
    opt = Option("NIFTY 24400 CE", exchange="NFO", strike=24400, expiry=date(2026, 8, 6),
                 option_type="CE", underlying_symbol="NIFTY")
    with pytest.raises(RateLimited):
        broker.get_lot_size(opt)


def test_orderbook_propagates_other_errors():
    """Non-rate transport errors propagate (H-5): returning an empty book
    would mask a dead broker during post-boot reconciliation and orphan orders
    would never be adopted."""
    from ntrade.domain.orders.book import OrderBook
    broker = make_broker(get_orderbook=lambda debug="NO": (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(RuntimeError, match="orderbook fetch failed"):
        broker.get_orderbook()


def test_expiry_date_still_degrades_on_other_errors():
    """Non-rate failures now raise RuntimeError (no silent [] degrade)."""
    broker = make_broker(get_expiry_date=lambda **kw: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(RuntimeError, match="expiry date"):
        broker.get_expiry_date(Index("NIFTY"), "OPTION")


def test_instrument_metadata_propagates_rate_limited():
    """A DH-904 on the instrument-file metadata read must raise RateLimited,
    never return {} (empty metadata looks like 'no tick/lot data').

    The transport's get_instrument_metadata reads ``self.tsl.instrument_df``
    (an attribute, not a method), so we mock the transport directly — the
    same pattern as test_order_status_rate_limited_not_swallowed."""
    from ntrade.execution.rate_limit import Quota, RateLimited
    broker = make_broker()
    broker._transport = MagicMock()
    broker._transport.get_instrument_metadata.side_effect = RateLimited(Quota.NON_TRADING)
    rel = Equity("RELIANCE")
    with pytest.raises(RateLimited):
        broker.get_instrument_metadata(rel)
