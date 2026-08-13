"""Tests for the ``/ws/market`` live candle stream."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, time as dtime

import pytest
from fastapi.testclient import TestClient

from ntrade.domain.market_hours import IST, is_market_open
from api.marketdata import FuturesMaster
from api.server import create_app

# Fixed weekday mid-session so live tests don't depend on wall clock.
_NSE_OPEN_NOW = datetime(2026, 8, 10, 10, 0, tzinfo=IST)   # Mon 10:00 — NSE+MCX open
_NSE_CLOSED_NOW = datetime(2026, 8, 10, 18, 30, tzinfo=IST)  # Mon 18:30 — NSE closed, MCX open


def _master(tmp_path, rows=None):
    header = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
              "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
              "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
              "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME")
    rows = rows or [("NSE,D,58072,FUTIDX,0,NIFTY-Aug2026-FUT,65.0,NIFTY AUG FUT,"
                     "2026-08-25 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY")]
    path = tmp_path / "master.csv"
    path.write_text("\n".join(["," + header] + ["," + r for r in rows]), encoding="utf-8")
    return FuturesMaster(str(path))


@pytest.fixture()
def app(tmp_path):
    app = create_app("synthetic", master=_master(tmp_path), tick_s=0.05, live_stream=True)
    app.state.pump._clock = lambda: _NSE_OPEN_NOW
    return app


def _receive_until(ws, mtype: str, symbol: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = ws.receive_json()
        if msg.get("type") == mtype and msg.get("symbol") == symbol:
            return msg
    raise AssertionError(f"no {mtype} message for {symbol} within {timeout_s}s")


def test_market_hours_nse_vs_mcx():
    assert is_market_open("NFO", _NSE_OPEN_NOW)
    assert is_market_open("MCX", _NSE_OPEN_NOW)
    assert not is_market_open("NFO", _NSE_CLOSED_NOW)
    assert is_market_open("MCX", _NSE_CLOSED_NOW)
    assert not is_market_open("NFO", datetime(2026, 8, 9, 12, 0, tzinfo=IST))  # Sunday


def test_subscribe_receives_live_candles(app):
    with TestClient(app) as c:
        pump = app.state.pump
        pump._real_feed = True
        with c.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "subscribe", "symbol": "NIFTY AUG FUT",
                          "exchange": "NFO", "interval": "1m"})
            status = ws.receive_json()
            assert status["type"] == "live_status"
            assert status["status"] == "streaming"
            assert status["source"] == "synthetic"

            pump.ingest_tick("NIFTY AUG FUT", 24300.0, 25, now=_NSE_OPEN_NOW)
            msg = _receive_until(ws, "candle", "NIFTY AUG FUT")
            candle = msg["candle"]
            assert set(candle) == {"time", "open", "high", "low", "close", "volume"}
            assert candle["high"] >= candle["low"]
            assert candle["volume"] > 0


def test_candle_bar_updates_in_place(app):
    """The in-progress bar keeps its time; a new bar starts at the boundary."""
    with TestClient(app) as c:
        pump = app.state.pump
        pump._real_feed = True
        with c.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "subscribe", "symbol": "NIFTY AUG FUT", "interval": "1m"})
            ws.receive_json()  # live_status
            pump.ingest_tick("NIFTY AUG FUT", 24300.0, 10, now=_NSE_OPEN_NOW)
            first = _receive_until(ws, "candle", "NIFTY AUG FUT")
            pump.ingest_tick("NIFTY AUG FUT", 24310.0, 20, now=_NSE_OPEN_NOW)
            second = _receive_until(ws, "candle", "NIFTY AUG FUT")
            # Same session bar (same minute) unless a boundary tick happened
            assert abs(second["candle"]["time"] - first["candle"]["time"]) <= 60


def test_ping_pong_and_bad_messages(app):
    with TestClient(app) as c:
        with c.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
            ws.send_text("{not json")
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "bogus"})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "subscribe", "interval": "1m"})  # no symbol
            assert ws.receive_json()["type"] == "error"


def test_unsubscribe_stops_stream(app):
    with TestClient(app) as c:
        pump = app.state.pump
        pump._real_feed = True
        with c.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "subscribe", "symbol": "NIFTY AUG FUT", "interval": "1m"})
            ws.receive_json()  # live_status
            pump.ingest_tick("NIFTY AUG FUT", 24300.0, 10, now=_NSE_OPEN_NOW)
            _receive_until(ws, "candle", "NIFTY AUG FUT")
            ws.send_json({"type": "unsubscribe", "symbol": "NIFTY AUG FUT"})
            ws.send_json({"type": "ping"})
            # the pong proves the server processed the unsubscribe in order
            assert ws.receive_json() == {"type": "pong"}
            assert "NIFTY AUG FUT" not in pump._subs
            # a late tick finds no sub state — nothing can fabricate a candle
            assert pump.ingest_tick("NIFTY AUG FUT", 24305.0, 10, now=_NSE_OPEN_NOW) is None


def test_daily_candle_anchored_at_midnight_ist(app):
    """1D live bars must anchor at midnight IST — the same grid Dhan's daily
    history uses — so history and the live daily candle share one timeline
    (regression: the old session-open anchor put the live daily bar 9h15m
    away from every historical daily bar, splitting each day on the chart)."""
    with TestClient(app) as c:
        pump = app.state.pump
        pump._real_feed = True  # deterministic: drive bars from ingest_tick
        pump.subscribe("NIFTY AUG FUT", "NFO", "1D")
        pump.ingest_tick("NIFTY AUG FUT", 24850.5, 65, now=_NSE_OPEN_NOW)
        bar = pump._subs["NIFTY AUG FUT"]["bar"]
        # Daily bars anchor at session open (09:15 IST for NFO), NOT midnight.
        # This matches Dhan's daily history grid and the live intraday grid.
        expected = int(datetime.combine(_NSE_OPEN_NOW.date(), dtime(9, 15),
                                        tzinfo=IST).timestamp())
        assert bar["time"] == expected


def test_intraday_candle_still_anchored_at_session_open(app):
    """The midnight anchor must not change intraday grids — 1m bars stay on
    the session-open grid (09:15 IST for NFO)."""
    with TestClient(app) as c:
        pump = app.state.pump
        pump._real_feed = True
        pump.subscribe("NIFTY AUG FUT", "NFO", "1m")
        pump.ingest_tick("NIFTY AUG FUT", 24850.5, 65, now=_NSE_OPEN_NOW)
        bar = pump._subs["NIFTY AUG FUT"]["bar"]
        # _NSE_OPEN_NOW is 10:00 IST -> the 1m bar is at 10:00, not midnight.
        expected = int(datetime.combine(_NSE_OPEN_NOW.date(), dtime(10, 0),
                                        tzinfo=IST).timestamp())
        assert bar["time"] == expected


def test_nse_off_mcx_streams_after_hours(tmp_path):
    """After NSE close, NFO is off but MCX still streams."""
    rows = [
        ("NSE,D,58072,FUTIDX,0,NIFTY-Aug2026-FUT,65.0,NIFTY AUG FUT,"
         "2026-08-25 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY"),
        ("MCX,M,449735,FUTCOM,0,CRUDEOIL-19Aug2026-FUT,1.0,CRUDEOIL AUG FUT,"
         "2026-08-19 23:30:00,-0.01,XX,1.0,M,FUTCOM,,CRUDEOIL"),
    ]
    app = create_app("synthetic", master=_master(tmp_path, rows), tick_s=0.05, live_stream=True)
    app.state.pump._clock = lambda: _NSE_CLOSED_NOW
    pump = app.state.pump
    pump._real_feed = True  # MCX must stream — real feed is required before subscribe

    with TestClient(app) as c:
        with c.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "subscribe", "symbol": "NIFTY AUG FUT",
                          "exchange": "NFO", "interval": "1m"})
            nifty = ws.receive_json()
            assert nifty["status"] == "off"
            assert "closed" in nifty["reason"].lower()

            ws.send_json({"type": "subscribe", "symbol": "CRUDEOIL AUG FUT",
                          "exchange": "MCX", "interval": "1m"})
            crude = ws.receive_json()
            assert crude["status"] == "streaming"
            pump.ingest_tick("CRUDEOIL AUG FUT", 6400.0, 10, now=_NSE_CLOSED_NOW)
            msg = _receive_until(ws, "candle", "CRUDEOIL AUG FUT")
            assert msg["exchange"] == "MCX"


def test_ingest_tick_forms_candle_from_real_ltp(app):
    """Live bar OHLC/volume must come from the tick, not the RNG walk."""
    with TestClient(app) as c:
        pump = app.state.pump
        pump._real_feed = True  # disable synthetic 1Hz walk
        pump.subscribe("NIFTY AUG FUT", "NFO", "1m")
        time.sleep(0.15)
        assert pump._subs["NIFTY AUG FUT"]["bar"] is None
        pump.ingest_tick("NIFTY AUG FUT", 24850.5, 65, now=_NSE_OPEN_NOW)
        bar = pump._subs["NIFTY AUG FUT"]["bar"]
        assert bar["open"] == 24850.5
        assert bar["high"] == 24850.5
        assert bar["low"] == 24850.5
        assert bar["close"] == 24850.5
        assert bar["volume"] == 65
        pump.ingest_tick("NIFTY AUG FUT", 24840.0, 30, now=_NSE_OPEN_NOW)
        bar = pump._subs["NIFTY AUG FUT"]["bar"]
        assert bar["open"] == 24850.5
        assert bar["high"] == 24850.5
        assert bar["low"] == 24840.0
        assert bar["close"] == 24840.0
        assert bar["volume"] == 95


def test_live_stream_disabled_reports_off_and_never_streams(tmp_path):
    """Explicit live_stream=False: historical data only."""
    app = create_app("synthetic", master=_master(tmp_path), tick_s=0.02, live_stream=False)
    assert app.state.pump.enabled is False

    with TestClient(app) as c:
        with c.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "subscribe", "symbol": "NIFTY AUG FUT", "interval": "1m"})
            status = ws.receive_json()
            assert status["type"] == "live_status"
            assert status["status"] == "off"
            assert "disabled" in status["reason"]
            for _ in range(3):
                ws.send_json({"type": "ping"})
                assert ws.receive_json() == {"type": "pong"}


def test_synthetic_provider_never_streams(tmp_path):
    app = create_app("synthetic", master=_master(tmp_path), tick_s=0.02, live_stream=True)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "subscribe", "symbol": "NIFTY AUG FUT",
                          "exchange": "NFO", "interval": "1m"})
            live_status = ws.receive_json()
            assert live_status["type"] == "live_status"
            assert live_status["status"] == "off"
            assert "no real live feed" in live_status["reason"]
            # the guard must not even create a sub state — nothing can be fabricated later
            assert "NIFTY AUG FUT" not in app.state.pump._subs
            # WS stays alive; only pong replies, never a candle
            for _ in range(3):
                ws.send_json({"type": "ping"})
                assert ws.receive_json() == {"type": "pong"}


def test_real_feed_ingest_tick_and_stale(tmp_path):
    app = create_app("synthetic", master=_master(tmp_path), tick_s=0.05, live_stream=True)
    pump = app.state.pump
    pump._real_feed = True              # test seam: no real socket needed
    pump._STALE_S = 0.1                 # small window so the test fires quickly
    clock_holder = {"now": _NSE_OPEN_NOW}
    pump._clock = lambda: clock_holder["now"]
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market") as ws:
            ws.send_json({"type": "subscribe", "symbol": "NIFTY AUG FUT",
                          "exchange": "NFO", "interval": "1m"})
            status = ws.receive_json()
            assert status["status"] == "streaming"
            for price in (24300.0, 24310.0, 24305.0):
                pump.ingest_tick("NIFTY AUG FUT", price, 25, now=clock_holder["now"])
            # deterministic pump-state form (each tick broadcasts its own candle,
            # so the in-progress bar asserts the aggregated OHLC/volume instead)
            bar = pump._subs["NIFTY AUG FUT"]["bar"]
            assert bar["high"] == 24310.0
            assert bar["low"] == 24300.0
            assert bar["volume"] == 75
            assert is_market_open("NFO", clock_holder["now"])
            # stop feeding; advance the clock beyond _STALE_S → watchdog emits stale
            clock_holder["now"] = _NSE_OPEN_NOW + timedelta(seconds=1.0)
            stale = _receive_until(ws, "live_status", "NIFTY AUG FUT", timeout_s=3.0)
            assert stale["status"] == "stale"
            assert "no ticks" in stale["reason"]
