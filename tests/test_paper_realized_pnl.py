"""realized_pnl must reflect closed round trips, not a structurally-zero sum.

Regression: _record_fill never stored a `pnl` key, so status() summed
f.get("pnl", 0.0) — always 0.0 — and the UI's PnL readout was MTM-only.
"""
from datetime import datetime

from api.paper_trader import PaperTraderService
from ntrade.events.order import OrderFilledEvent

TS = datetime(2026, 8, 21, 9, 15)


def _svc():
    svc = PaperTraderService.__new__(PaperTraderService)
    svc._fills = []
    return svc


def _fill(order_id, side, qty, price):
    return OrderFilledEvent(order_id=order_id, symbol="NIFTY", exchange="NFO",
                            side=side, quantity=qty, fill_price=price, ts=TS)


def test_round_trip_realizes_pnl():
    svc = _svc()
    svc._record_fill(_fill("1", "BUY", 10, 100.0))
    svc._record_fill(_fill("2", "SELL", 10, 110.0))
    assert svc._fills[-1]["pnl"] == 100.0


def test_partial_close_realizes_pro_rata():
    svc = _svc()
    svc._record_fill(_fill("1", "BUY", 10, 100.0))
    svc._record_fill(_fill("2", "SELL", 4, 105.0))
    assert svc._fills[-1]["pnl"] == 20.0


def test_short_round_trip_realizes_pnl():
    """Short legs: SELL opens, BUY covers — the cover realizes (entry - exit)."""
    svc = _svc()
    svc._record_fill(_fill("1", "SELL", 10, 100.0))
    svc._record_fill(_fill("2", "BUY", 10, 95.0))
    assert svc._fills[-1]["pnl"] == 50.0
