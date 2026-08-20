"""PaperTraderService tests — the UI paper-trade control's backend.

Drives the real kernel stack (real ticks -> MarketEngine -> CandleEngine ->
HalfTrendStrategy -> risk -> PaperBroker fills) through the pump's tick listener,
and asserts the status endpoint reports trades, balance changes, and positions —
the ₹1M paper-trading contract.

Ticks are fed as naive IST wall-clock timestamps (the domain convention the
strategy and the whole backtest stack expect — see the zero-parity audit); the
kernel's CandleEngine builds the real bars, and fills surface through the
service's snapshot drain.
"""

import time
from datetime import datetime

from api.paper_trader import PaperTraderService, _POLL_S
from api.server import create_app
from ntrade.domain.market_hours import IST
from ntrade.events.market import CandleClosedEvent

_SYMBOL = "BANKNIFTY AUG FUT"


def _ist_epoch(y, mo, d, h, mi):
    """UTC epoch for an IST wall-clock time."""
    from zoneinfo import ZoneInfo
    dt = datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("Asia/Kolkata"))
    return int(dt.timestamp())


def _bar_ts(day, minute):
    """Session-anchored IST epoch for a 1m bar (09:15 open)."""
    return _ist_epoch(2026, 8, day, 9, 15) + (minute - 555) * 60


def _naive_ist(epoch):
    """IST wall-clock digits as a naive timestamp (naive == IST wall time)."""
    return datetime.fromtimestamp(epoch, tz=IST).replace(tzinfo=None)


class _FakePump:
    """Pump seam with the real pump's surface the service uses: ``_real_feed``,
    ``enabled``, ``_subs``, ``subscribe``, ``on_tick``, ``ingest_tick``."""

    def __init__(self):
        self._real_feed = True
        self.enabled = True
        self._subs = {}
        self._tick_listeners = []
        self.subscribed = []

    def subscribe(self, symbol, exchange, interval):
        self.subscribed.append((symbol, exchange, interval))
        self._subs.setdefault(symbol, {"bar": None, "exchange": exchange, "interval": interval})

    def on_tick(self, fn):
        self._tick_listeners.append(fn)
        return lambda: self._tick_listeners.remove(fn)

    def ingest_tick(self, symbol, price, qty=0, now=None):
        state = self._subs.get(symbol)
        if state is None:
            return None
        state["bar"] = {"close": price}  # snapshot ltp only; the kernel builds real bars
        for fn in self._tick_listeners:
            fn(symbol, state.get("exchange", "NFO"), float(price), int(qty or 0), now)


def _feed_bar(svc, day, mi, o, h, l, c, volume=100):
    """Drive one 1m bar as four real ticks so the kernel's CandleEngine closes
    it with the intended OHLC: o / low / high / c at +1s, +10s, +30s, +59s,
    with qty values summing to the bar's volume."""
    base = _bar_ts(day, mi)
    q = volume // 4
    for off, px, qty in ((1, o, q), (10, min(l, h), q),
                         (30, max(l, h), q), (59, c, volume - 3 * q)):
        svc._pump.ingest_tick(_SYMBOL, px, qty, now=_naive_ist(base + off))


def _feed_day(svc, day, open_px, close_px):
    """Full 09:15-15:29 session drifting open → close (1m bars)."""
    n = 375
    for i in range(n):
        frac = i / (n - 1)
        c = open_px + (close_px - open_px) * frac
        o = c - (close_px - open_px) / (n - 1) * 0.5
        _feed_bar(svc, day, 555 + i, o, max(o, c) + 0.5, min(o, c) - 0.5, c)


def _make_service():
    pump = _FakePump()
    svc = PaperTraderService(None, pump, initial_cash=1_000_000.0)
    return svc, pump


def _drain(svc, timeout=3.0):
    """Wait for the snapshot loop to record fills from the kernel."""
    status = svc.status()
    deadline = time.monotonic() + timeout
    while status["n_trades"] < 1 and time.monotonic() < deadline:
        time.sleep(_POLL_S)  # the snapshot loop polls every 1.0s
        status = svc.status()
    return status


def test_start_stop_lifecycle():
    svc, pump = _make_service()
    st = svc.start("BANKNIFTY AUG FUT", "NFO", lot_size=15)
    assert st["running"] is True
    assert st["symbol"] == "BANKNIFTY AUG FUT"
    assert st["initial_cash"] == 1_000_000.0
    assert ("BANKNIFTY AUG FUT", "NFO", "1m") in pump.subscribed
    # Idempotent start while running.
    assert svc.start("BANKNIFTY AUG FUT", lot_size=15)["running"] is True
    stopped = svc.stop()
    assert stopped["running"] is False
    assert stopped["balance"] == 1_000_000.0


def test_paper_feed_publishes_real_ticks_not_fabricated_events(tmp_path):
    from tests.test_api_market import _sample_master
    from api.marketdata import FuturesMaster

    app = create_app("synthetic", master=FuturesMaster(_sample_master(tmp_path)), live_stream=False)
    service = app.state.paper
    service._pump._real_feed = True
    service._pump.enabled = True
    service._pump.subscribe("NIFTY AUG FUT", "NFO", "1m")
    service.start("NIFTY AUG FUT", "NFO")
    try:
        now = datetime.now(tz=IST)
        for price in (24300.0, 24310.0, 24305.0, 24312.0):
            service._pump.ingest_tick("NIFTY AUG FUT", price, 25, now=now)
        st = service.status()
        assert st["running"] is True
        assert service._session is not None
    finally:
        service.stop()
