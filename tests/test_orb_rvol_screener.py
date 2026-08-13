"""ORB + RVOL 09:45 screener — integration against the real parquet store.

Contract:
  - as_of with no 09:45 equity bar → StoreStaleError (fail closed)
  - hits have rvol >= 2 and 15m open+close both outside previous-session range
  - PDH/PDL ignore after-hours (15:30+) bars still sitting in the store
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ntrade.data.parquet_store import ParquetStorage

_STORE = Path("data/ohlcv")
_AS_OF = date(2026, 7, 31)  # last full cash session in the store


@pytest.fixture(scope="module")
def store() -> ParquetStorage:
    if not _STORE.exists():
        pytest.skip("parquet store missing")
    return ParquetStorage("data")


def test_fails_closed_when_0945_bar_missing(store):
    from datetime import timedelta

    from ntrade.data.orb_screener import StoreStaleError, screen_orb_rvol

    # Resolve a date the store genuinely cannot have a 09:45 bar for, so the
    # fail-closed contract keeps holding as the store is backfilled (a
    # hard-coded future date like 2026-08-11 silently broke once August data
    # landed). Use the day after the last session that actually has a 09:45
    # equity bar — that next calendar day is never in the store.
    import duckdb

    con = duckdb.connect()
    store.duckdb_scan(con, start="2020-01-01", end="2099-01-01")
    last = con.execute(
        """
        SELECT MAX(CAST(timestamp AS DATE)) FROM ohlcv
        WHERE kind = 'equity' AND timeframe = '1m'
          AND timestamp::TIME = TIME '09:45:00'
        """
    ).fetchone()[0]
    assert last is not None, "store has no equity 09:45 bars at all"
    absent = (pd.Timestamp(last).date() + timedelta(days=1))

    with pytest.raises(StoreStaleError):
        screen_orb_rvol(store, as_of=absent)


def test_jul31_hits_are_outside_pd_range_with_rvol_2x(store):
    from ntrade.data.orb_screener import screen_orb_rvol
    hits = screen_orb_rvol(store, as_of=_AS_OF)
    assert isinstance(hits, pd.DataFrame)
    assert not hits.empty
    assert (hits["rvol"] >= 2.0).all()
    above = (hits["orb_open"] >= hits["pdh_prev"]) & (hits["orb_close"] >= hits["pdh_prev"])
    below = (hits["orb_open"] <= hits["pdl_prev"]) & (hits["orb_close"] <= hits["pdl_prev"])
    assert (above | below).all()
    assert hits["signal"].isin(["BUY", "SELL"]).all()
    assert above.equals(hits["signal"] == "BUY")


def test_pdh_ignores_after_hours_bars(store):
    """PDH/PDL are 09:15–15:30 only — after-hours highs still on disk must not leak."""
    from datetime import time as dtime
    from ntrade.data.orb_screener import screen_orb_rvol

    hits = screen_orb_rvol(store, as_of=_AS_OF)
    assert not hits.empty
    after_hours_rows = 0
    for row in hits.itertuples():
        raw = store.read(
            symbols=[row.symbol],
            start=str(row.prev_date),
            end=f"{row.prev_date} 23:59:59",
            timeframe="1m",
        )
        t = raw["timestamp"].dt.time
        session = raw[(t >= dtime(9, 15)) & (t < dtime(15, 30))]
        after_hours_rows += int((t >= dtime(15, 30)).sum())
        assert float(row.pdh_prev) == pytest.approx(float(session["high"].max()))
        assert float(row.pdl_prev) == pytest.approx(float(session["low"].min()))
    assert after_hours_rows > 0  # store still has after-hours tape; PDH didn't use it
