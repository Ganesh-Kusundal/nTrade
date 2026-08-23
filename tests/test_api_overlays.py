"""Integration tests for the backend-owned chart + catalog endpoints (M3).

These prove the API serves candles + overlays + strategy markers from the
single Python calc path, and that /catalog mirrors the registries so the FE
can become a renderer (drop its client-side `run` wiring).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.marketdata import FuturesMaster
from api.server import create_app


@pytest.fixture()
def client(tmp_path):
    header = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
              "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
              "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
              "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME")
    rows = [
        ("NSE,D,58067,FUTIDX,0,BANKNIFTY-Aug2026-FUT,30.0,BANKNIFTY AUG FUT,"
         "2026-08-25 14:30:00,-0.01,XX,20.0,M,FUT,,BANKNIFTY"),
        ("NSE,D,58072,FUTIDX,0,NIFTY-Aug2026-FUT,65.0,NIFTY AUG FUT,"
         "2026-08-25 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY"),
    ]
    path = tmp_path / "master.csv"
    path.write_text("\n".join(["," + header] + ["," + r for r in rows]), encoding="utf-8")
    app = create_app("synthetic", master=FuturesMaster(path))
    with TestClient(app) as c:
        yield c


def test_chart_endpoint_returns_overlays(client):
    r = client.get("/api/market/chart", params={
        "symbol": "NIFTY AUG FUT", "exchange": "NFO", "interval": "1m", "limit": 60,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "NIFTY AUG FUT"
    assert len(body["candles"]) > 0
    ov = body["overlays"]
    assert ov["vwap"] is not None and len(ov["vwap"]) == len(body["candles"])
    assert ov["volume_profile"] is not None
    assert {"poc", "vah", "val", "levels"} <= set(ov["volume_profile"])


def test_chart_endpoint_with_strategy(client):
    r = client.get("/api/market/chart", params={
        "symbol": "NIFTY AUG FUT", "exchange": "NFO", "interval": "1m",
        "limit": 120, "strategy": "halftrend",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"] is not None
    assert body["strategy"]["id"] == "halftrend"
    assert body["strategy"]["markers"] is not None or body["strategy"]["series"] is not None


def test_catalog_endpoint(client):
    r = client.get("/api/market/catalog")
    assert r.status_code == 200
    body = r.json()
    ids = {i["id"] for i in body["indicators"]}
    assert {"vwap", "volume_profile", "halftrend"} <= ids
    strat_ids = {s["id"] for s in body["strategies"]}
    assert {"halftrend", "orb_vwap"} <= strat_ids
