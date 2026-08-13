"""Paper trading REST route tests (FastAPI TestClient)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api.server import create_app


def test_paper_routes_lifecycle(tmp_path):
    from tests.test_api_market import _sample_master

    app = create_app("synthetic", master=_sample_master(tmp_path), live_stream=False)
    with TestClient(app) as c:
        # Status is idle before any session.
        st = c.get("/api/paper/status").json()
        assert st["running"] is False
        assert st["initial_cash"] == 1_000_000.0

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
