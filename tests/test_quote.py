"""Tests for the Quote value object."""

from datetime import datetime, timedelta

from ntrade.domain.market.quote import Quote, Tick


def test_quote_defaults():
    q = Quote.empty()
    assert q.ltp == 0.0
    assert q.spread() == 0.0


def test_quote_derived_values():
    q = Quote(ltp=100.0, bid=99.9, ask=100.1, prev_close=95.0)
    assert q.spread() == 0.2
    assert q.mid_price() == 100.0
    assert q.change() == 5.0
    assert round(q.change_pct(), 4) == round(5 / 95 * 100, 4)


def test_quote_immutability():
    q = Quote(ltp=100.0, bid=99.9, ask=100.1)
    q2 = q.with_update(ltp=101.0)
    assert q.ltp == 100.0  # original untouched
    assert q2.ltp == 101.0
    assert q2.bid == 99.9  # other fields preserved


def test_quote_staleness():
    old = Quote(ltp=10.0, timestamp=datetime.now() - timedelta(seconds=60))
    fresh = Quote(ltp=10.0, timestamp=datetime.now())
    assert old.is_stale(max_age_seconds=5)
    assert not fresh.is_stale(max_age_seconds=5)
    assert Quote.empty().is_stale()


def test_tick_as_dict():
    t = Tick(symbol="NIFTY", price=24383.6, quantity=75, side="buy")
    d = t.as_dict()
    assert d["symbol"] == "NIFTY"
    assert d["price"] == 24383.6
    assert d["kind"] == "trade"


def test_is_stale_with_explicit_now():
    ts = datetime(2026, 1, 1, 10, 0, 0)
    q = Quote(ltp=100.0, timestamp=ts)
    assert not q.is_stale(now=datetime(2026, 1, 1, 10, 0, 4))  # 4s < 5s default
    assert q.is_stale(now=datetime(2026, 1, 1, 10, 0, 6))      # 6s > 5s default


def test_live_stream_ticks_are_bounded():
    from ntrade.domain.market.stream import LiveStream
    from ntrade.domain.market.quote import Tick, Quote
    from datetime import datetime
    from unittest.mock import MagicMock

    inst = MagicMock()
    inst._quote = Quote.empty()
    stream = LiveStream(inst)
    for i in range(15_000):
        tick = Tick(symbol="X", price=float(i), kind="quote", timestamp=datetime.now())
        stream.ingest_tick(tick)
    assert len(stream._ticks) == 10_000
