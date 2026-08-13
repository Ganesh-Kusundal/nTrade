"""Unit tests for the api market-data service (DTOs, master, providers)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api.marketdata import (
    Candle,
    FuturesMaster,
    MarketDataError,
    MarketDataService,
    SyntheticProvider,
    build_service,
    candle_from_row,
    chart_start,
    normalize_candles,
)
from ntrade.domain.constants import Exchange


# ------------------------------------------------------------------ candle DTO


def test_candle_dto_shape():
    c = Candle(time=1_700_000_000, open=100.0, high=101.5, low=99.0,
               close=100.75, volume=1234)
    d = c.as_dict()
    assert d == {"time": 1_700_000_000, "open": 100.0, "high": 101.5,
                 "low": 99.0, "close": 100.75, "volume": 1234}


def test_candle_from_row_naive_timestamp_is_ist():
    # Naive timestamps are interpreted as IST wall time (domain convention)
    dt = datetime(2026, 8, 3, 9, 15)  # IST 09:15
    c = candle_from_row({"timestamp": dt, "open": 1, "high": 2, "low": 0.5,
                         "close": 1.5, "volume": 10})
    assert c is not None
    # 09:15 IST == 03:45 UTC
    expected = datetime(2026, 8, 3, 3, 45, tzinfo=timezone.utc).timestamp()
    assert c.time == int(expected)


def test_normalize_candles_sorts_dedupes_and_clamps():
    rows = [
        {"timestamp": datetime(2026, 8, 3, 9, 17), "open": 3, "high": 3, "low": 3,
         "close": 3, "volume": 1},
        {"timestamp": datetime(2026, 8, 3, 9, 15), "open": 1, "high": 1, "low": 1,
         "close": 1, "volume": 1},
        {"timestamp": datetime(2026, 8, 3, 9, 15), "open": 9, "high": 9, "low": 9,
         "close": 9, "volume": 9},  # duplicate time — first wins
        {"timestamp": datetime(2026, 8, 3, 9, 16), "open": 2, "high": 2, "low": 2,
         "close": 2, "volume": 1},
    ]
    out = normalize_candles(rows)
    assert [c["time"] for c in out] == sorted(c["time"] for c in out)
    assert len(out) == 3
    out2 = normalize_candles(rows, limit=2)
    assert len(out2) == 2
    assert out2[-1]["time"] == out[-1]["time"]  # keeps the most recent


# --------------------------------------------------------------------- master


def _write_master_csv(tmp_path, rows: list[str]) -> str:
    header = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
              "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
              "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
              "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME")
    # Real dumps carry a leading empty index column on header AND rows
    path = tmp_path / "all_instrument.csv"
    path.write_text("\n".join(["," + header] + ["," + r for r in rows]), encoding="utf-8")
    return str(path)


_FUT_NIFTY_AUG = ("NSE,D,58072,FUTIDX,0,NIFTY-Aug2026-FUT,65.0,NIFTY AUG FUT,"
                  "2026-08-25 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY")
_FUT_NIFTY_SEP = ("NSE,D,58073,FUTIDX,0,NIFTY-Sep2026-FUT,65.0,NIFTY SEP FUT,"
                  "2026-09-29 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY")
_FUT_BANK_AUG = ("NSE,D,58067,FUTIDX,0,BANKNIFTY-Aug2026-FUT,30.0,BANKNIFTY AUG FUT,"
                 "2026-08-25 14:30:00,-0.01,XX,20.0,M,FUT,,BANKNIFTY")
_FUT_FINNIFTY_AUG = ("NSE,D,58066,FUTIDX,0,FINNIFTY-Aug2026-FUT,40.0,FINNIFTY AUG FUT,"
                     "2026-08-25 14:30:00,-0.01,XX,5.0,M,FUT,,FINNIFTY")
_OPT_ROW = ("NSE,D,58090,OPTIDX,0,NIFTY-Aug2026-25000-CE,65.0,NIFTY 25 AUG 25000 CALL,"
            "2026-08-25 14:30:00,25000.0,CE,10.0,M,OPT,,NIFTY")
_FUTCOM_CRUDE_AUG = ("MCX,M,449735,FUTCOM,0,CRUDEOIL-19Aug2026-FUT,1.0,CRUDEOIL AUG FUT,"
                     "2026-08-19 23:30:00,-0.01,XX,1.0,M,FUTCOM,,CRUDEOIL")


def test_master_parses_futures_only(tmp_path):
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG, _OPT_ROW]))
    assert m.loaded
    assert m.roots() == ["NIFTY"]
    contracts = m.contracts("NIFTY")
    assert len(contracts) == 1
    c = contracts[0]
    assert c.symbol == "NIFTY AUG FUT"
    assert c.contract_id == "NIFTY-Aug2026-FUT"
    assert c.expiry == date(2026, 8, 25)
    assert c.lot_size == 65.0
    assert c.tick_size == 10.0
    assert c.is_index


def test_master_sorts_by_expiry_and_resolves(tmp_path):
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_SEP, _FUT_NIFTY_AUG]))
    assert [c.contract_id for c in m.contracts("NIFTY")] == [
        "NIFTY-Aug2026-FUT", "NIFTY-Sep2026-FUT"]
    assert m.front_month("NIFTY").contract_id == "NIFTY-Aug2026-FUT"
    assert m.resolve("NIFTY SEP FUT").contract_id == "NIFTY-Sep2026-FUT"
    assert m.resolve("NIFTY 2026-08-25").contract_id == "NIFTY-Aug2026-FUT"
    assert m.resolve("NOPE") is None


def test_master_index_roots_first(tmp_path):
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_BANK_AUG, _FUT_NIFTY_AUG]))
    assert m.index_roots() == ["BANKNIFTY", "NIFTY"]


def test_master_missing_file():
    m = FuturesMaster("/nonexistent/nope.csv")
    assert not m.loaded
    assert m.roots() == []


def test_master_skips_empty_newest_dump(tmp_path, monkeypatch):
    """Tradehull can leave a 0-byte all_instrument dump; fall back to prior day."""
    from pathlib import Path
    import api.marketdata as md

    empty = tmp_path / "all_instrument 2026-08-10.csv"
    empty.write_bytes(b"")
    good = Path(_write_master_csv(tmp_path, [_FUTCOM_CRUDE_AUG, _FUT_NIFTY_AUG]))
    # Newest mtime wins if empty isn't filtered — touch empty after good.
    empty.touch()
    monkeypatch.setattr(md, "_glob_instrument_csvs", lambda: [empty, good])
    m = FuturesMaster()
    assert m.loaded
    assert m.source_path == good
    assert m.contracts("CRUDEOIL")
    assert m.contracts("NIFTY")


def test_master_loads_mcx_futcom(tmp_path):
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUTCOM_CRUDE_AUG, _OPT_ROW]))
    assert m.contracts("CRUDEOIL")
    c = m.contracts("CRUDEOIL")[0]
    assert c.symbol == "CRUDEOIL AUG FUT"
    assert c.exchange == Exchange.MCX
    assert c.expiry == date(2026, 8, 19)
    assert m.resolve("CRUDEOIL AUG FUT").contract_id == "CRUDEOIL-19Aug2026-FUT"


# -------------------------------------------------------------- synthetic provider


def test_synthetic_candles_deterministic_and_shaped():
    a = SyntheticProvider(FuturesMaster())
    b = SyntheticProvider(FuturesMaster())
    rows_a = a.candles(symbol="NIFTY OCT FUT", exchange="NFO", interval="5m")
    rows_b = b.candles(symbol="NIFTY OCT FUT", exchange="NFO", interval="5m")
    assert rows_a == rows_b
    assert len(rows_a) == 8 * 75  # 8 sessions x 75 five-minute bars
    first, last = rows_a[0], rows_a[-1]
    for c in (first, last):
        assert set(c) == {"time", "open", "high", "low", "close", "volume"}
        assert c["high"] >= max(c["open"], c["close"])
        assert c["low"] <= min(c["open"], c["close"])
        assert c["volume"] > 0
    assert rows_a[0]["time"] < rows_a[-1]["time"]


def test_synthetic_time_range_filter():
    p = SyntheticProvider(FuturesMaster())
    rows = p.candles(symbol="NIFTY OCT FUT", exchange="NFO", interval="1m",
                     start=datetime(2026, 8, 5), end=datetime(2026, 8, 6, 23, 59))
    assert rows
    assert all(datetime.fromtimestamp(c["time"]).date() <= date(2026, 8, 6)
               for c in rows)
    assert len(p.candles(symbol="NIFTY OCT FUT", exchange="NFO", interval="1m", limit=10)) == 10


def test_synthetic_quote_has_change_fields():
    p = SyntheticProvider(FuturesMaster())
    q = p.quote(symbol="NIFTY OCT FUT", exchange="NFO")
    assert q["ltp"] > 0
    assert "change" in q and "change_pct" in q
    assert q["source"] == "synthetic"


def test_synthetic_lot_size_from_master(tmp_path):
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    p = SyntheticProvider(m)
    rows = p.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="5m")
    # volume is lot_size * (~30..250) so never below 65*30
    assert all(c["volume"] >= 65 * 30 for c in rows)


# -------------------------------------------------------------- dhan provider


class _FakeBroker:
    """Minimal DhanBroker stand-in: fixed quote + historical frame."""

    connected = True

    def __init__(self, quote, hist):
        self._quote = quote
        self._hist = hist
        self.hist_calls = 0
        self.lt_calls: list[dict] = []

    def get_quote(self, inst):
        return self._quote

    def get_historical(self, inst, timeframe="5m", days=None, start=None, end=None):
        self.hist_calls += 1
        return self._hist

    def get_long_term_historical(self, inst, timeframe="1d", from_date=None, to_date=None):
        self.hist_calls += 1
        self.lt_calls.append({"timeframe": timeframe, "from_date": from_date, "to_date": to_date})
        return self._hist


class _FakeFactory:
    def __init__(self):
        self.calls: list[tuple] = []

    def index(self, root):
        self.calls.append(("index", root))
        return object()

    def commodity(self, root):
        self.calls.append(("commodity", root))
        return object()

    def future(self, underlying, expiry):
        self.calls.append(("future", expiry))
        return object()


def _dhan_provider(broker, master=None):
    from api.marketdata import DhanProvider

    p = DhanProvider(master or FuturesMaster())
    p._broker = broker
    p._factory = _FakeFactory()
    return p


def _daily_frame():
    import pandas as pd

    # 15:29 — the last valid intraday session bar (bars at/after the 15:30
    # close are post-close ghosts the provider now clamps).
    return pd.DataFrame({
        "timestamp": [datetime(2026, 8, 6, 15, 29), datetime(2026, 8, 7, 15, 29)],
        "open": [58000.0, 58100.0],
        "high": [58050.0, 58175.0],
        "low": [57950.0, 58025.0],
        "close": [58020.0, 58140.0],
        "volume": [1000, 1200],
    })


def _as_date(value):
    # datetime is a date subclass — check it first or .date() never runs.
    if isinstance(value, datetime):
        return value.date()
    return value


def test_dhan_candles_asks_broker_for_90_days_including_today(tmp_path):
    """Direct DhanProvider (no dates) hits the dated broker endpoint for 90
    calendar days through today IST — not an undated dump, not the parquet lake.
    Chart load goes through MarketDataService and defaults to 3 weekdays."""
    from zoneinfo import ZoneInfo

    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    broker = _FakeBroker(None, _daily_frame())
    p = _dhan_provider(broker, master=m)
    rows = p.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m")
    assert rows
    assert broker.lt_calls, "dated long-term fetch required; undated get_historical is the data-lake-shaped dump"
    call = broker.lt_calls[0]
    today = datetime.now(tz=ZoneInfo("Asia/Kolkata")).date()
    to_d = _as_date(call["to_date"])
    from_d = _as_date(call["from_date"])
    assert to_d == today
    assert (to_d - from_d).days == 90
    assert call["timeframe"] == "1m"


def test_dhan_provider_builds_mcx_future_from_commodity(tmp_path):
    """MCX FUTCOM contracts must use commodity underlying, not index."""
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUTCOM_CRUDE_AUG]))
    p = _dhan_provider(_FakeBroker(None, _daily_frame()), master=m)
    rows = p.candles(symbol="CRUDEOIL AUG FUT", exchange="MCX", interval="1D")
    assert rows
    assert ("commodity", "CRUDEOIL") in p._factory.calls
    assert ("index", "CRUDEOIL") not in p._factory.calls


def test_dhan_quote_falls_back_to_daily_history_when_enrichment_empty():
    from ntrade.domain.market.quote import Quote

    # Broker quote has LTP + volume only (quote-data enrichment failed live:
    # the transport swallows the error, leaving OHLC/prev_close at zero).
    broker = _FakeBroker(Quote(ltp=58200.0, volume=379050), _daily_frame())
    p = _dhan_provider(broker)
    q = p.quote(symbol="NIFTY AUG FUT", exchange="NFO")
    assert q["source"] == "dhan"
    assert q["ltp"] == 58200.0
    assert q["open"] == 58100.0 and q["high"] == 58175.0 and q["low"] == 58025.0
    assert q["prev_close"] == 58020.0
    assert q["volume"] == 379050  # LTP volume wins over history
    assert q["change"] == round(58200.0 - 58020.0, 2)
    assert broker.hist_calls == 1


def test_dhan_candles_write_through_to_parquet_roundtrips(tmp_path):
    """DhanProvider persists fetched futures candles into the Parquet
    datalake; ParquetProvider reads the same wire bars back (naive-IST storage
    convention round-trips exactly to UTC epochs)."""
    from api.marketdata import DhanProvider, ParquetProvider

    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    p = DhanProvider(m, data_dir=str(tmp_path / "lake"))
    p._broker = _FakeBroker(None, _daily_frame())
    p._factory = _FakeFactory()

    rows = p.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1D")
    assert rows  # 2 daily bars from the fake broker

    pp = ParquetProvider(m, base_path=str(tmp_path / "lake"))
    back = pp.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1D")
    assert back == rows
    assert [c["time"] for c in back] == sorted(c["time"] for c in rows)


def test_parquet_quote_derives_daily_from_intraday(tmp_path):
    """Offline stores often lack D1 — the parquet quote aggregates the stored
    1m rows into daily bars so the header never fails offline."""
    from api.marketdata import DhanProvider, ParquetProvider

    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    p = DhanProvider(m, data_dir=str(tmp_path / "lake"))
    # 1m must sit inside the session mask (t < 15:30); 15:30 is a daily close stamp.
    frame = _daily_frame()
    frame["timestamp"] = [datetime(2026, 8, 6, 15, 29), datetime(2026, 8, 7, 15, 29)]
    p._broker = _FakeBroker(None, frame)
    p._factory = _FakeFactory()
    p.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m")  # store 1m only

    pp = ParquetProvider(m, base_path=str(tmp_path / "lake"))
    assert pp.candles(symbol="NIFTY AUG FUT", exchange="NFO",
                      interval="1D") == []  # no D1 in the store
    q = pp.quote(symbol="NIFTY AUG FUT", exchange="NFO")
    assert q["source"] == "parquet"
    assert q["ltp"] == 58140.0  # last 1m close == the day's close
    assert q["high"] == 58175.0 and q["low"] == 58025.0  # last day's extremes
    assert q["prev_close"] == 58020.0  # previous day's close


def test_dhan_write_through_skips_unsupported_roots(tmp_path):
    """Only NIFTY/BANKNIFTY futures land in the datalake — an unsupported
    root's candles are served but never persisted."""
    from api.marketdata import DhanProvider

    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_FINNIFTY_AUG]))
    p = DhanProvider(m, data_dir=str(tmp_path / "lake"))
    p._broker = _FakeBroker(None, _daily_frame())
    p._factory = _FakeFactory()

    rows = p.candles(symbol="FINNIFTY AUG FUT", exchange="NFO", interval="1D")
    assert rows
    assert list((tmp_path / "lake").rglob("data.parquet")) == []


def test_dhan_quote_prefers_broker_enrichment():
    from ntrade.domain.market.quote import Quote

    broker = _FakeBroker(
        Quote(ltp=58200.0, open=57990.0, high=58250.0, low=57900.0,
              prev_close=58020.0, volume=10, oi=42),
        _daily_frame(),
    )
    p = _dhan_provider(broker)
    q = p.quote(symbol="NIFTY AUG FUT", exchange="NFO")
    assert q["open"] == 57990.0 and q["high"] == 58250.0
    assert q["prev_close"] == 58020.0 and q["oi"] == 42
    assert broker.hist_calls == 0  # no fallback fetch needed


def test_aggregate_daily_bars_anchored_at_midnight():
    """Aggregated daily bars must sit on the midnight-IST grid (the same grid
    Dhan's daily history and the live 1D pump use) — the old first-intraday-bar
    stamp put them 9h15m away from every other daily candle."""
    from api.marketdata import _aggregate_daily

    ist = ZoneInfo("Asia/Kolkata")
    rows = [
        {"time": int(datetime(2026, 8, 6, 9, 15, tzinfo=ist).timestamp()),
         "open": 1.0, "high": 3.0, "low": 0.5, "close": 2.0, "volume": 10},
        {"time": int(datetime(2026, 8, 6, 15, 25, tzinfo=ist).timestamp()),
         "open": 2.0, "high": 4.0, "low": 1.0, "close": 3.0, "volume": 20},
    ]
    out = _aggregate_daily(rows)
    assert len(out) == 1
    assert out[0]["time"] == int(datetime(2026, 8, 6, 0, 0, tzinfo=ist).timestamp())
    assert out[0]["close"] == 3.0 and out[0]["high"] == 4.0 and out[0]["low"] == 0.5
    assert out[0]["volume"] == 30


def test_synthetic_daily_bars_anchored_at_midnight():
    """Synthetic 1D bars must share the midnight-IST grid of Dhan's daily
    history and the live pump's 1D anchor (regression: they used the 09:15
    session-open anchor, splitting the daily chart into two sections)."""
    ist = ZoneInfo("Asia/Kolkata")
    p = SyntheticProvider(FuturesMaster())
    rows = p.candles(symbol="NIFTY OCT FUT", exchange="NFO", interval="1D")
    assert rows
    hours = {datetime.fromtimestamp(c["time"], tz=ist).strftime("%H:%M")
             for c in rows}
    assert hours == {"00:00"}


class _FixedProvider:
    """Provider that serves exactly the given wire rows (service-level tests)."""

    name = "fixed"

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def candles(self, *, symbol, exchange, interval, start=None, end=None,
                limit=None):
        rows = list(self._rows)
        if limit is not None:
            rows = rows[-limit:]
        return rows

    def quote(self, *, symbol, exchange):
        return {"ltp": 0.0, "source": self.name}


def test_service_drops_post_close_history_bars():
    """Dhan history ships bars that start after the session close (1m to 15:39
    on NSE) while the live pump stops exactly at 15:30. The service must strip
    those ghost bars so the history tail matches the live grid — otherwise the
    chart breaks at the right edge after the historical load."""
    ist = ZoneInfo("Asia/Kolkata")
    rows = [
        {"time": int(datetime(2026, 8, 12, 9, 15, tzinfo=ist).timestamp()),
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
        {"time": int(datetime(2026, 8, 12, 15, 25, tzinfo=ist).timestamp()),
         "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 2},
        # ghost bars: Dhan reports 1m through 15:39 (and a bar AT 15:30)
        {"time": int(datetime(2026, 8, 12, 15, 30, tzinfo=ist).timestamp()),
         "open": 3.0, "high": 3.0, "low": 3.0, "close": 3.0, "volume": 3},
        {"time": int(datetime(2026, 8, 12, 15, 39, tzinfo=ist).timestamp()),
         "open": 4.0, "high": 4.0, "low": 4.0, "close": 4.0, "volume": 4},
    ]
    svc = MarketDataService(_FixedProvider(rows))
    out = svc.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m",
                      start=datetime(2026, 8, 12, 0, 0, tzinfo=ist),
                      end=datetime(2026, 8, 12, 23, 59, tzinfo=ist))
    times = [datetime.fromtimestamp(c["time"], tz=ist).strftime("%H:%M")
             for c in out]
    assert times == ["09:15", "15:25"]


def test_service_uses_mcx_session_close_for_clamp():
    """The clamp must use the exchange's own close — MCX trades to 23:30, so
    a 23:30 bar is post-close there while a 23:25 bar is a valid session bar."""
    ist = ZoneInfo("Asia/Kolkata")
    rows = [
        {"time": int(datetime(2026, 8, 12, 23, 25, tzinfo=ist).timestamp()),
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
        {"time": int(datetime(2026, 8, 12, 23, 30, tzinfo=ist).timestamp()),
         "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 2},
    ]
    svc = MarketDataService(_FixedProvider(rows))
    out = svc.candles(symbol="CRUDEOIL AUG FUT", exchange="MCX", interval="5m",
                      start=datetime(2026, 8, 12, 0, 0, tzinfo=ist),
                      end=datetime(2026, 8, 13, 0, 0, tzinfo=ist))
    assert len(out) == 1
    assert datetime.fromtimestamp(out[0]["time"], tz=ist).strftime("%H:%M") == "23:25"


def test_service_does_not_clamp_daily_bars():
    """Daily bars anchor at midnight (before the open), so the session-close
    clamp must not apply to the 1D interval."""
    ist = ZoneInfo("Asia/Kolkata")
    rows = [
        {"time": int(datetime(2026, 8, 12, 0, 0, tzinfo=ist).timestamp()),
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
    ]
    svc = MarketDataService(_FixedProvider(rows))
    out = svc.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1D",
                      start=datetime(2026, 8, 12, 0, 0, tzinfo=ist),
                      end=datetime(2026, 8, 12, 23, 59, tzinfo=ist))
    assert len(out) == 1


# --------------------------------------------------------------------- service


def test_build_service_synthetic_default():
    svc = build_service("synthetic")
    assert svc.name == "synthetic"
    d = svc.describe()
    assert d["provider"] == "synthetic"
    assert d["live"] is False


def test_build_service_unknown_provider():
    with pytest.raises(MarketDataError):
        build_service("bogus")


def test_service_roots_only_nifty_banknifty(tmp_path):
    """The terminal's root list shows NIFTY/BANKNIFTY only, even when the
    master carries more index futures (FINNIFTY, ...)."""
    m = FuturesMaster(_write_master_csv(
        tmp_path, [_FUT_NIFTY_AUG, _FUT_BANK_AUG, _FUT_FINNIFTY_AUG]))
    svc = build_service("synthetic", master=m)
    assert [r["root"] for r in svc.roots()] == ["BANKNIFTY", "NIFTY"]


def test_service_roots_contracts_and_contract_fields(tmp_path):
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG, _FUT_NIFTY_SEP]))
    svc = build_service("synthetic", master=m)
    roots = svc.roots()
    assert [r["root"] for r in roots] == ["NIFTY"]
    assert roots[0]["front_month"]["contract_id"] == "NIFTY-Aug2026-FUT"
    contracts = svc.contracts("NIFTY")
    assert contracts[0]["is_front_month"] is True
    assert contracts[1]["is_front_month"] is False
    assert contracts[0]["exchange"] == Exchange.DERIVATIVES
    assert contracts[0]["expiry"] == "2026-08-25"


def test_service_candles_passthrough_and_limit(tmp_path):
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    svc = build_service("synthetic", master=m)
    rows = svc.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="15m", limit=20)
    assert len(rows) == 20


def test_service_candles_defaults_to_three_weekdays(tmp_path):
    """UI chart window: omitting start/end must not dump the full provider history."""
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    svc = build_service("synthetic", master=m)
    rows = svc.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m")
    ist = ZoneInfo("Asia/Kolkata")
    days = {datetime.fromtimestamp(c["time"], tz=ist).date() for c in rows}
    assert 1 <= len(days) <= 3
    assert all(d.weekday() < 5 for d in days)


def test_chart_start_scales_beyond_21_calendar_days():
    """chart_start must handle windows wider than ~15 weekdays — the old fixed
    21-calendar-day scan cap silently truncated anything longer."""
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=ist)
    start = chart_start(now, days=20)
    # 20 weekdays back from Wed 12 Aug 2026 = Thu 16 Jul 2026.
    assert start.date() == date(2026, 7, 16)
    # The floor is exactly 20 weekdays of history, no more, no less.
    d = start.date()
    kept = 0
    while d <= now.date():
        if d.weekday() < 5:
            kept += 1
        d += timedelta(days=1)
    assert kept == 20
    # The 3-day default is unchanged.
    assert chart_start(now, days=3).date() == date(2026, 8, 10)


def test_synthetic_provider_serves_window_wider_than_default(tmp_path):
    """A chart window wider than DEFAULT_DAYS (8) sessions must not be
    truncated: the synthetic provider lazily extends its session grid to the
    requested start instead of returning only its built-in sessions."""
    ist = ZoneInfo("Asia/Kolkata")
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    svc = build_service("synthetic", master=m)
    now = datetime.now(tz=ist)
    start = chart_start(now, days=12)
    rows = svc.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m",
                       start=start, end=now)
    days = {datetime.fromtimestamp(c["time"], tz=ist).date() for c in rows}
    assert len(days) == 12  # the full 12-session window, not the default 8
    assert min(days) == start.date()


def test_service_ticks_deterministic_and_bar_faithful(tmp_path):
    """Replay ticks (compact per-bar form): seeded (same result twice), one
    tick per second per bar, and the bar's O/H/L/C are genuinely anchored
    (first=open, last=close, extremes touched) with the bar's volume fully
    distributed across its ticks."""
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    svc = build_service("synthetic", master=m)
    a = svc.ticks(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m", limit=2)
    b = svc.ticks(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m", limit=2)
    assert a == b  # deterministic per bar (seeded by symbol|bar time)
    assert len(a) == 2  # two bars, compact per-bar entries
    assert [set(x) for x in a] == [{"time", "prices", "quantities"}] * 2
    assert all(len(x["prices"]) == 60 and len(x["quantities"]) == 60 for x in a)

    bars = svc.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m", limit=2)
    prices = [p for x in a for p in x["prices"]]
    assert a[0]["time"] == bars[0]["time"]
    assert a[0]["prices"][0] == bars[0]["open"]
    assert a[-1]["prices"][-1] == bars[-1]["close"]
    assert max(prices) == max(b["high"] for b in bars)
    assert min(prices) == min(b["low"] for b in bars)
    assert sum(q for x in a for q in x["quantities"]) == sum(b["volume"] for b in bars)


def test_service_ticks_budget_trims_recent_bars(tmp_path, monkeypatch):
    """MAX_TICKS caps the payload; the FIRST bars (replay start) get ticks,
    recent bars beyond the budget fall back to plain bars on the client."""
    import api.marketdata as md

    monkeypatch.setattr(md, "MAX_TICKS", 120)
    m = FuturesMaster(_write_master_csv(tmp_path, [_FUT_NIFTY_AUG]))
    svc = build_service("synthetic", master=m)
    bars_all = svc.candles(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m")
    ticks = svc.ticks(symbol="NIFTY AUG FUT", exchange="NFO", interval="1m")
    assert sum(len(b["prices"]) for b in ticks) == 120  # budget -> only 2 bars survive
    assert ticks[0]["time"] == bars_all[0]["time"]  # the replay-start bars
    assert ticks[-1]["time"] < bars_all[2]["time"]
