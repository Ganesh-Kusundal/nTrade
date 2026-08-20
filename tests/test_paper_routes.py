"""Paper trading REST route tests (FastAPI TestClient)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api.server import create_app


def test_paper_routes_lifecycle(tmp_path):
    from tests.test_api_market import _sample_master
    from api.marketdata import FuturesMaster

    app = create_app("synthetic", master=FuturesMaster(_sample_master(tmp_path)), live_stream=False)
    with TestClient(app) as c:
        # Status is idle before any session.
        st = c.get("/api/paper/status").json()
        assert st["running"] is False
        assert st["initial_cash"] == 1_000_000.0

        # Paper now requires a real live feed — open the fixture seams so the
        # start control can run without a Dhan connection.
        app.state.paper._pump._real_feed = True
        app.state.paper._pump.enabled = True

        # Start a paper session on a symbol (idempotent second start).
        st = c.post("/api/paper/start",
                    json={"symbol": "NIFTY AUG FUT", "exchange": "NFO"}).json()
        assert st["running"] is True
        assert st["symbol"] == "NIFTY AUG FUT"
        assert st["balance"] == 1_000_000.0
        assert c.post("/api/paper/start",
                      json={"symbol": "NIFTY AUG FUT"}).json()["running"] is True

        # Stop flattens to idle.
        st = c.post("/api/paper/stop").json()
        assert st["running"] is False
        assert st["balance"] == 1_000_000.0


def test_paper_start_requires_symbol():
    app = create_app("synthetic", live_stream=False)
    with TestClient(app) as c:
        # Missing symbol → 422 from the pydantic body.
        assert c.post("/api/paper/start", json={}).status_code == 422


def test_paper_start_requires_real_feed():
    app = create_app("synthetic", live_stream=False)
    c = TestClient(app)
    r = c.post("/api/paper/start", json={"symbol": "NIFTY AUG FUT", "exchange": "NFO"})
    assert r.status_code == 422
    assert "real live feed" in r.json()["detail"]


def test_paper_lot_size_from_master(tmp_path):
    """Lot size comes from the instrument master contract, not a hardcoded map.

    ``NIFTY AUG FUT`` is lot 65 in the master fixture but the old hardcoded map
    returned 75 for any NIFTY symbol — the master value wins.
    """
    from tests.test_api_market import _sample_master
    from api.marketdata import FuturesMaster

    app = create_app("synthetic", master=FuturesMaster(_sample_master(tmp_path)), live_stream=False)
    app.state.paper._pump._real_feed = True
    app.state.paper._pump.enabled = True
    c = TestClient(app)
    r = c.post("/api/paper/start", json={"symbol": "NIFTY AUG FUT", "exchange": "NFO"})
    assert r.status_code == 200
    assert r.json()["lot_size"] == 65


def test_paper_unknown_lot_size_requires_explicit(tmp_path):
    """A symbol with no master contract must fail loudly, not mint a default."""
    from tests.test_api_market import _sample_master
    from api.marketdata import FuturesMaster

    app = create_app("synthetic", master=FuturesMaster(_sample_master(tmp_path)), live_stream=False)
    app.state.paper._pump._real_feed = True
    app.state.paper._pump.enabled = True
    c = TestClient(app)
    r = c.post("/api/paper/start", json={"symbol": "NOSUCH FUT", "exchange": "NFO"})
    assert r.status_code == 422
    assert "lot size" in r.json()["detail"].lower()


def _master(tmp_path):
    header = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
              "SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,"
              "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
              "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME")
    rows = [("NSE,D,58072,FUTIDX,0,NIFTY-Aug2026-FUT,65.0,NIFTY AUG FUT,"
             "2026-08-25 14:30:00,-0.01,XX,10.0,M,FUT,,NIFTY")]
    path = tmp_path / "master.csv"
    path.write_text("\n".join(["," + header] + ["," + r for r in rows]), encoding="utf-8")
    from api.marketdata import FuturesMaster
    return FuturesMaster(str(path))


def test_paper_accepts_strategy_id_and_reports_snapshot(tmp_path):
    """Plan M5: paper accepts a strategy id; status() returns the snapshot from
    the SAME instance the kernel runs (zero-parity with chart + live)."""
    from api.marketdata import FuturesMaster

    app = create_app("synthetic", master=_master(tmp_path), live_stream=False)
    app.state.paper._pump._real_feed = True
    app.state.paper._pump.enabled = True
    c = TestClient(app)
    st = c.post("/api/paper/start", json={
        "symbol": "NIFTY AUG FUT", "exchange": "NFO", "strategy": "valentini",
    }).json()
    assert st["running"] is True
    assert st["strategy"] is not None
    assert st["strategy"]["id"] == "valentini"
    # Backward-compat: omitting strategy still defaults to morning_vah_val.
    c.post("/api/paper/stop")
    st2 = c.post("/api/paper/start", json={
        "symbol": "NIFTY AUG FUT", "exchange": "NFO",
    }).json()
    assert st2["strategy"]["id"] == "morning_vah_val"


def test_paper_status_strategy_snapshot_on_idle(tmp_path):
    """Plan M5: idle status reports strategy=None (no session)."""
    app = create_app("synthetic", live_stream=False)
    c = TestClient(app)
    assert c.get("/api/paper/status").json()["strategy"] is None