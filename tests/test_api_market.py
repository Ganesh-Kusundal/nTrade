"""Integration tests for the market REST endpoints (FastAPI TestClient)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.marketdata import FuturesMaster
from api.server import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app("synthetic", master=FuturesMaster(_sample_master(tmp_path)))
    with TestClient(app) as c:
        yield c


def _sample_master(tmp_path) -> Path:
    header = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
              "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
              "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
              "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME")
    rows = [
        ("NSE,D,58072,FUTIDX,0,NIFTY-Aug2026-FUT,65.0,NIFTY AUG FUT,"
         "2026-08-25 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY"),
        ("NSE,D,58073,FUTIDX,0,NIFTY-Sep2026-FUT,65.0,NIFTY SEP FUT,"
         "2026-09-29 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY"),
        ("NSE,D,58067,FUTIDX,0,BANKNIFTY-Aug2026-FUT,30.0,BANKNIFTY AUG FUT,"
         "2026-08-25 14:30:00,-0.01,XX,20.0,M,FUT,,BANKNIFTY"),
        ("MCX,M,449735,FUTCOM,0,CRUDEOIL-19Aug2026-FUT,1.0,CRUDEOIL AUG FUT,"
         "2026-08-19 23:30:00,-0.01,XX,1.0,M,FUTCOM,,CRUDEOIL"),
        # Unsupported root — the /roots list must filter it out
        ("NSE,D,58066,FUTIDX,0,FINNIFTY-Aug2026-FUT,40.0,FINNIFTY AUG FUT,"
         "2026-08-25 14:30:00,-0.01,XX,5.0,M,FUT,,FINNIFTY"),
    ]
    path = tmp_path / "master.csv"
    path.write_text("\n".join(["," + header] + ["," + r for r in rows]), encoding="utf-8")
    return path


# ------------------------------------------------------------------- happy path


def test_ticks_endpoint_shape_and_validation(client):
    r = client.get("/api/market/ticks", params={"symbol": "NIFTY AUG FUT", "interval": "1m", "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["interval"] == "1m"
    assert body["seconds"] == 60
    assert body["count"] == 120
    assert len(body["bars"]) == 2
    assert set(body["bars"][0]) == {"time", "prices", "quantities"}
    assert len(body["bars"][0]["prices"]) == 60
    # Same validation taxonomy as /candles
    assert client.get("/api/market/ticks", params={"symbol": "NIFTY AUG FUT", "interval": "7m"}).status_code == 422
    assert client.get("/api/market/ticks", params={"symbol": "NIFTY AUG FUT!!", "interval": "1m"}).status_code == 422


def test_provider_endpoint(client):
    r = client.get("/api/market/provider")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "synthetic"
    assert body["instrument_master_loaded"] is True


def test_roots_endpoint(client):
    r = client.get("/api/market/roots")
    assert r.status_code == 200
    roots = r.json()["roots"]
    assert [x["root"] for x in roots] == ["BANKNIFTY", "NIFTY", "CRUDEOIL"]
    assert roots[0]["front_month"]["contract_id"] == "BANKNIFTY-Aug2026-FUT"
    crude = next(x for x in roots if x["root"] == "CRUDEOIL")
    assert crude["exchange"] == "MCX"
    assert crude["front_month"]["symbol"] == "CRUDEOIL AUG FUT"


def test_contracts_endpoint(client):
    r = client.get("/api/market/roots/NIFTY/contracts")
    assert r.status_code == 200
    body = r.json()
    assert body["root"] == "NIFTY"
    assert body["exchange"] == "NFO"
    assert len(body["contracts"]) == 2
    c = body["contracts"][0]
    assert c["contract_id"] == "NIFTY-Aug2026-FUT"
    assert c["symbol"] == "NIFTY AUG FUT"
    assert c["is_front_month"] is True
    assert c["lot_size"] == 65.0
    assert c["tick_size"] == 10.0


def test_mcx_contracts_and_candles(client):
    r = client.get("/api/market/roots/CRUDEOIL/contracts")
    assert r.status_code == 200
    body = r.json()
    assert body["exchange"] == "MCX"
    assert body["contracts"][0]["exchange"] == "MCX"
    assert body["contracts"][0]["symbol"] == "CRUDEOIL AUG FUT"
    candles = client.get(
        "/api/market/candles",
        params={"symbol": "CRUDEOIL AUG FUT", "exchange": "MCX", "interval": "5m", "limit": 10},
    )
    assert candles.status_code == 200
    assert candles.json()["count"] > 0
    assert candles.json()["exchange"] == "MCX"


def test_candles_endpoint_dto(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "5m"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "synthetic"
    assert body["interval"] == "5m"
    assert body["count"] > 0
    first = body["candles"][0]
    assert set(first) == {"time", "open", "high", "low", "close", "volume"}
    assert first["time"] > 0
    assert first["high"] >= first["close"] >= first["low"]


def test_candles_ascending_and_unique(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "1m"})
    times = [c["time"] for c in r.json()["candles"]]
    assert times == sorted(times)
    assert len(times) == len(set(times))


def test_candles_interval_variants(client):
    for interval in ("1m", "5m", "15m", "1h", "1D"):
        r = client.get("/api/market/candles",
                       params={"symbol": "NIFTY AUG FUT", "interval": interval})
        assert r.status_code == 200, interval
        assert r.json()["count"] > 0, interval


def test_candles_time_range_filter(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "5m",
                           "start": "2026-08-04T00:00:00", "end": "2026-08-05T23:59:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] <= 8 * 75
    from datetime import datetime, timezone
    lo = datetime(2026, 8, 4, tzinfo=timezone.utc).timestamp()
    hi = datetime(2026, 8, 5, 23, 59, tzinfo=timezone.utc).timestamp()
    assert all(lo <= c["time"] <= hi for c in body["candles"])


def test_candles_empty_range_returns_empty_list(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "5m",
                           "start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"})
    assert r.status_code == 200
    assert r.json()["candles"] == []


def test_quote_endpoint(client):
    r = client.get("/api/market/quote", params={"symbol": "NIFTY AUG FUT"})
    assert r.status_code == 200
    q = r.json()
    assert q["ltp"] > 0
    assert q["source"] == "synthetic"
    assert set(["change", "change_pct", "high", "low", "open"]) <= set(q)


def test_health(client):
    r = client.get("/api/market/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# -------------------------------------------------------------------- errors


def test_bad_interval_422(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "2m"})
    assert r.status_code == 422


def test_bad_symbol_422(client):
    assert client.get("/api/market/candles",
                      params={"symbol": "BAD!!!"}).status_code == 422
    assert client.get("/api/market/quote",
                      params={"symbol": "B A D !"}).status_code == 422


def test_unknown_root_404(client):
    assert client.get("/api/market/roots/NOPE/contracts").status_code == 404


def test_limit_clamped(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "5m", "limit": 7})
    assert r.status_code == 200
    assert r.json()["count"] == 7


def test_reversed_range_422(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "5m",
                           "start": "2026-08-10T00:00:00", "end": "2026-08-01T00:00:00"})
    assert r.status_code == 422


def test_bad_iso_range_422(client):
    r = client.get("/api/market/candles",
                   params={"symbol": "NIFTY AUG FUT", "interval": "5m",
                           "start": "not-a-date"})
    assert r.status_code == 422


def test_master_missing_503(tmp_path):
    # Empty master (no CSV anywhere) -> roots endpoint 503, not a crash
    from api.marketdata import FuturesMaster
    app = create_app("synthetic", master=FuturesMaster(str(tmp_path / "none.csv")))
    with TestClient(app) as c:
        assert c.get("/api/market/roots").status_code == 503
