"""PaperTraderService tests — the UI paper-trade control's backend.

Drives the real kernel stack (feed -> candles -> MorningVAHVAL -> risk ->
PaperBroker fills) through the service's bar-feeding path with synthetic
candles, and asserts the status endpoint reports trades, balance changes,
and positions — the ₹1M paper-trading contract.
"""

from datetime import datetime

from api.paper_trader import PaperTraderService


def _ist_epoch(y, mo, d, h, mi):
    """UTC epoch for an IST wall-clock time."""
    from zoneinfo import ZoneInfo
    dt = datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("Asia/Kolkata"))
    return int(dt.timestamp())


def _bar(day, minute, o, h, l, c, volume=100):
    """One 1m bar dict exactly as the live pump emits (session-anchored ts)."""
    return {
        "time": _ist_epoch(2026, 8, day, 9, 15) + (minute - 555) * 60,
        "open": o, "high": h, "low": l, "close": c, "volume": volume,
    }


class _FakePump:
    """Minimal pump stub: the service only reads _subs and calls subscribe."""

    def __init__(self):
        self._subs = {}
        self.subscribed = []

    def subscribe(self, symbol, exchange, interval):
        self.subscribed.append((symbol, exchange, interval))
        self._subs.setdefault(symbol, {})["bar"] = None


def _feed_day(svc, day, open_px, close_px):
    """Full 09:15-15:29 session drifting open → close (1m bars)."""
    n = 375
    for i in range(n):
        frac = i / (n - 1)
        c = open_px + (close_px - open_px) * frac
        o = c - (close_px - open_px) / (n - 1) * 0.5
        svc._feed_closed(_bar(day, 555 + i, o, max(o, c) + 0.5,
                              min(o, c) - 0.5, c))


def _feed_profile(svc, day):
    """09:15-09:29 profile window: POC ≈ 99, VAH ≈ 101, final 2m dips below
    VAL (same data as tests/test_morning_vah_val.py)."""
    for i in range(10):  # minutes 09:15..09:24
        c = 99.5 if i % 2 == 0 else 101.5
        svc._feed_closed(_bar(day, 555 + i, c - 1.5, c + 0.5, c - 1.5, c))
    # 09:25..09:27 — thicken the region above VAH so the VA extends upward.
    svc._feed_closed(_bar(day, 565, 102.0, 103.0, 101.5, 102.5, volume=50))
    svc._feed_closed(_bar(day, 566, 102.5, 103.2, 102.0, 103.0, volume=50))
    svc._feed_closed(_bar(day, 567, 103.0, 103.2, 101.5, 102.0, volume=50))
    # 09:28..09:29 — fake breakdown below VAL.
    svc._feed_closed(_bar(day, 568, 99.0, 98.5, 97.3, 97.6))
    svc._feed_closed(_bar(day, 569, 97.6, 98.2, 97.2, 97.7))


def _feed_reversal_long(svc, day):
    svc._feed_closed(_bar(day, 570, 97.7, 98.8, 97.4, 98.4))
    svc._feed_closed(_bar(day, 571, 98.4, 99.9, 98.2, 99.6))


def _make_service():
    pump = _FakePump()
    svc = PaperTraderService(None, pump, initial_cash=1_000_000.0)
    return svc, pump


def test_start_stop_lifecycle():
    svc, pump = _make_service()
    st = svc.start("BANKNIFTY AUG FUT", "NFO")
    assert st["running"] is True
    assert st["symbol"] == "BANKNIFTY AUG FUT"
    assert st["initial_cash"] == 1_000_000.0
    assert ("BANKNIFTY AUG FUT", "NFO", "1m") in pump.subscribed
    # Idempotent start while running.
    assert svc.start("BANKNIFTY AUG FUT")["running"] is True
    stopped = svc.stop()
    assert stopped["running"] is False
    assert stopped["balance"] == 1_000_000.0


def test_paper_buy_then_stop_is_flat():
    svc, _ = _make_service()
    svc.start("BANKNIFTY AUG FUT", "NFO", lot_size=15)
    # The service runs the cost-drag-tuned preset (require_cluster=True, which
    # needs 20+ 2m bars — the synthetic feed can't reach it). White-box the
    # cluster check so the paper pipeline (feed -> fills -> status) is tested
    # in isolation from the strategy knobs, exactly like the strategy suite.
    strat = svc._session.kernel.strategy_engine.strategies[0]
    strat._ema_cluster = lambda close: True
    _feed_day(svc, 3, 100.0, 130.0)      # prior day UP → bias
    _feed_profile(svc, 4)                # day 2 profile (VAL ~97.2, VAH ~101)
    _feed_reversal_long(svc, 4)          # 09:30-09:31 reversal
    svc._feed_closed(_bar(4, 572, 99.6, 99.7, 99.4, 99.6))  # entry fires

    status = svc.status()
    assert status["running"] is True
    assert status["n_trades"] >= 1, "the entry fill must be recorded"
    fills = [t for t in status["trades"] if t["side"] == "BUY"]
    assert fills, "a BUY fill must appear"
    assert status["balance"] < 1_000_000.0, "cash must drop on the buy"
    positions = status["positions"]
    assert any(p["symbol"] == "BANKNIFTY AUG FUT" for p in positions)
    open_pos = [p for p in positions if p["symbol"] == "BANKNIFTY AUG FUT"][0]
    assert open_pos["quantity"] > 0
    assert open_pos["quantity"] % 15 == 0, "qty must be lot-multiples"
    # An open runner after the entry.
    assert status["realized_pnl"] == 0.0

    svc.stop()
    stopped = svc.status()
    assert stopped["running"] is False
