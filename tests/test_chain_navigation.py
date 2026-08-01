"""Tests for OptionChain navigation: Expiry, OptionPair, expiries(), expiry(), pairs()."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.instruments.cash import Index
from ntrade.domain.instruments.chain import OptionChain
from ntrade.domain.instruments.derivatives import Option
from ntrade.domain.instruments.expiry import Expiry, OptionPair


# -------------------------------------------------------------------- helpers

def _make_option(symbol: str, strike: float, expiry: date, otype: str,
                 ltp: float = 10.0, oi: int = 5000, broker=None) -> Option:
    opt = Option(
        symbol=symbol, exchange="NFO",
        strike=strike, expiry=expiry, option_type=otype,
        underlying_symbol="NIFTY", broker=broker,
    )
    opt._quote = opt._quote.with_update(ltp=ltp, oi=oi)
    return opt


def _build_chain(expiries: list[date] | None = None, strikes: list[float] | None = None,
                 spot: float = 24500.0) -> OptionChain:
    """Build a multi-expiry chain with known values."""
    if expiries is None:
        expiries = [date(2026, 8, 6), date(2026, 8, 13)]
    if strikes is None:
        atm = round(spot / 50) * 50
        strikes = [atm + (i - 2) * 50 for i in range(5)]  # 5 strikes centred on ATM
    broker = PaperBroker()
    underlying = Index("NIFTY", broker=broker)
    underlying._quote = underlying._quote.with_update(ltp=spot)
    options = []
    for d in expiries:
        for s in strikes:
            for otype in ("CE", "PE"):
                ltp = max(1.0, abs(spot - s) * 0.1 + 5.0)
                oi = 10000 + int(s) * 10
                options.append(_make_option(
                    f"NIFTY {s} {d} {otype}", s, d, otype,
                    ltp=ltp, oi=oi, broker=broker,
                ))
    return OptionChain(underlying, options, atm_strike=round(spot / 50) * 50)


# ================================================================ OptionPair

class TestOptionPair:
    def test_straddle_premium(self):
        ce = _make_option("CE", 24500, date(2026, 8, 6), "CE", ltp=120.0)
        pe = _make_option("PE", 24500, date(2026, 8, 6), "PE", ltp=80.0)
        pair = OptionPair(strike=24500, call=ce, put=pe)
        assert pair.straddle_premium == 200.0

    def test_straddle_premium_missing_leg(self):
        ce = _make_option("CE", 24500, date(2026, 8, 6), "CE", ltp=120.0)
        pair = OptionPair(strike=24500, call=ce, put=None)
        assert pair.straddle_premium == 120.0

    def test_pcr(self):
        ce = _make_option("CE", 24500, date(2026, 8, 6), "CE", oi=20000)
        pe = _make_option("PE", 24500, date(2026, 8, 6), "PE", oi=30000)
        pair = OptionPair(strike=24500, call=ce, put=pe)
        assert pair.pcr == 1.5

    def test_pcr_zero_call_oi(self):
        ce = _make_option("CE", 24500, date(2026, 8, 6), "CE", oi=0)
        pe = _make_option("PE", 24500, date(2026, 8, 6), "PE", oi=1000)
        pair = OptionPair(strike=24500, call=ce, put=pe)
        assert pair.pcr == 0.0

    def test_synthetic_long_price(self):
        ce = _make_option("CE", 24500, date(2026, 8, 6), "CE", ltp=150.0)
        pe = _make_option("PE", 24500, date(2026, 8, 6), "PE", ltp=100.0)
        pair = OptionPair(strike=24500, call=ce, put=pe)
        assert pair.synthetic_long_price() == 50.0

    def test_synthetic_short_price(self):
        ce = _make_option("CE", 24500, date(2026, 8, 6), "CE", ltp=150.0)
        pe = _make_option("PE", 24500, date(2026, 8, 6), "PE", ltp=100.0)
        pair = OptionPair(strike=24500, call=ce, put=pe)
        assert pair.synthetic_short_price() == -50.0

    def test_frozen(self):
        pair = OptionPair(strike=24500)
        with pytest.raises(AttributeError):
            pair.strike = 24550  # type: ignore[misc]


# ==================================================================== Expiry

class TestExpiry:
    def test_atm_returns_centre_pair(self):
        opts = [
            _make_option("24400CE", 24400, date(2026, 8, 6), "CE"),
            _make_option("24400PE", 24400, date(2026, 8, 6), "PE"),
            _make_option("24450CE", 24450, date(2026, 8, 6), "CE"),
            _make_option("24450PE", 24450, date(2026, 8, 6), "PE"),
            _make_option("24500CE", 24500, date(2026, 8, 6), "CE"),
            _make_option("24500PE", 24500, date(2026, 8, 6), "PE"),
        ]
        exp = Expiry(date(2026, 8, 6), opts, atm_strike=24450)
        pair = exp.atm()
        assert pair.strike == 24450
        assert pair.call is not None and pair.call.option_type == "CE"
        assert pair.put is not None and pair.put.option_type == "PE"

    def test_atm_offset(self):
        opts = [
            _make_option("CE", s, date(2026, 8, 6), "CE")
            for s in (24400, 24450, 24500, 24550, 24600)
        ] + [
            _make_option("PE", s, date(2026, 8, 6), "PE")
            for s in (24400, 24450, 24500, 24550, 24600)
        ]
        exp = Expiry(date(2026, 8, 6), opts, atm_strike=24500)
        # offset +1 → next strike above ATM
        pair = exp.atm(offset=1)
        assert pair.strike == 24550

    def test_pair_at_exact_strike(self):
        opts = [
            _make_option("CE", 24500, date(2026, 8, 6), "CE"),
            _make_option("PE", 24500, date(2026, 8, 6), "PE"),
        ]
        exp = Expiry(date(2026, 8, 6), opts, atm_strike=24500)
        pair = exp.pair_at(24500)
        assert pair.strike == 24500

    def test_pair_at_missing_raises(self):
        exp = Expiry(date(2026, 8, 6), [], atm_strike=24500)
        with pytest.raises(KeyError):
            exp.pair_at(99999)

    def test_otm_returns_correct_options(self):
        opts = [
            _make_option("CE", s, date(2026, 8, 6), "CE")
            for s in (24400, 24450, 24500, 24550, 24600)
        ] + [
            _make_option("PE", s, date(2026, 8, 6), "PE")
            for s in (24400, 24450, 24500, 24550, 24600)
        ]
        exp = Expiry(date(2026, 8, 6), opts, atm_strike=24500)
        otm = exp.otm(2)
        assert len(otm) == 2
        # OTM calls above ATM, OTM puts below ATM
        types = {(o.strike, o.option_type) for o in otm}
        assert (24550, "CE") in types
        assert (24450, "PE") in types

    def test_itm_returns_correct_options(self):
        opts = [
            _make_option("CE", s, date(2026, 8, 6), "CE")
            for s in (24400, 24450, 24500, 24550, 24600)
        ] + [
            _make_option("PE", s, date(2026, 8, 6), "PE")
            for s in (24400, 24450, 24500, 24550, 24600)
        ]
        exp = Expiry(date(2026, 8, 6), opts, atm_strike=24500)
        itm = exp.itm(2)
        assert len(itm) == 2
        types = {(o.strike, o.option_type) for o in itm}
        # ITM calls below ATM, ITM puts above ATM
        assert (24450, "CE") in types
        assert (24550, "PE") in types

    def test_pairs_returns_all_strikes(self):
        opts = [
            _make_option("CE", s, date(2026, 8, 6), "CE")
            for s in (24400, 24450, 24500)
        ] + [
            _make_option("PE", s, date(2026, 8, 6), "PE")
            for s in (24400, 24450, 24500)
        ]
        exp = Expiry(date(2026, 8, 6), opts, atm_strike=24450)
        pairs = exp.pairs()
        assert len(pairs) == 3
        assert [p.strike for p in pairs] == [24400, 24450, 24500]

    def test_strikes_sorted(self):
        opts = [
            _make_option("CE", s, date(2026, 8, 6), "CE")
            for s in (24500, 24400, 24450)
        ]
        exp = Expiry(date(2026, 8, 6), opts, atm_strike=24450)
        assert exp.strikes() == [24400, 24450, 24500]

    def test_repr(self):
        exp = Expiry(date(2026, 8, 6), [], atm_strike=24500)
        assert "2026-08-06" in repr(exp)


# ================================================================ OptionChain

class TestOptionChainNavigation:
    def test_expiries_returns_expiry_objects(self):
        chain = _build_chain()
        exps = chain.expiries()
        assert len(exps) == 2
        assert all(isinstance(e, Expiry) for e in exps)
        assert exps[0].date == date(2026, 8, 6)
        assert exps[1].date == date(2026, 8, 13)

    def test_expiry_offset_zero_is_nearest(self):
        chain = _build_chain()
        exp = chain.expiry(0)
        assert exp.date == date(2026, 8, 6)

    def test_expiry_offset_one_is_next(self):
        chain = _build_chain()
        exp = chain.expiry(1)
        assert exp.date == date(2026, 8, 13)

    def test_expiry_offset_clamped(self):
        chain = _build_chain()
        exp = chain.expiry(999)
        assert exp.date == date(2026, 8, 13)  # last available

    def test_expiry_empty_raises(self):
        broker = PaperBroker()
        underlying = Index("NIFTY", broker=broker)
        chain = OptionChain(underlying, [], atm_strike=24500)
        with pytest.raises(ValueError, match="No expiries"):
            chain.expiry(0)

    def test_pairs_returns_all_pairs(self):
        chain = _build_chain()
        pairs = chain.pairs()
        # 2 expiries × 5 strikes = 10 pairs
        assert len(pairs) == 10
        assert all(isinstance(p, OptionPair) for p in pairs)

    def test_at_strike_o1(self):
        chain = _build_chain()
        # Should find the option in O(1)
        opt = chain.at_strike(24500, "CE")
        assert opt is not None
        assert opt.strike == 24500
        assert opt.option_type == "CE"

    def test_at_strike_missing(self):
        chain = _build_chain()
        assert chain.at_strike(99999) is None

    def test_at_strike_no_type_returns_first(self):
        chain = _build_chain()
        opt = chain.at_strike(24500)
        assert opt is not None
        assert opt.strike == 24500

    def test_nearest_expiry_still_works(self):
        chain = _build_chain()
        assert chain.nearest_expiry == date(2026, 8, 6)

    def test_strikes_uses_index(self):
        chain = _build_chain()
        strikes = chain.strikes
        assert len(strikes) == 5
        assert strikes == sorted(strikes)

    def test_expiry_atm_delegates_to_expiry(self):
        chain = _build_chain()
        exp = chain.expiry(0)
        pair = exp.atm()
        assert pair.call is not None
        assert pair.put is not None
