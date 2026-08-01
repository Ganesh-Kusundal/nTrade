"""Tests for option analytics: Black-Scholes, Greeks, Option, OptionChain."""

from datetime import date

import pandas as pd
import pytest

from ntrade.brokers.paper import PaperBroker
from ntrade.domain.analytics.greeks import BlackScholes, Greeks
from ntrade.domain.instruments.cash import Equity, Index
from ntrade.domain.instruments.chain import OptionChain
from ntrade.domain.instruments.derivatives import Option


# Dhan-specific chain parsing now lives in the Dhan adapter (domain stays
# broker-agnostic); alias here so the test reads naturally.
from ntrade.brokers.dhan import _chain_from_dhan_df  # noqa: E402


def test_black_scholes_call_price_bounds():
    price = BlackScholes.price(spot=100, strike=100, years=0.5, risk_free=0.065, sigma=0.2, option_type="CE")
    assert 0 < price < 100
    put = BlackScholes.price(spot=100, strike=100, years=0.5, risk_free=0.065, sigma=0.2, option_type="PE")
    assert 0 < put < 100


def test_black_scholes_put_call_parity():
    spot, strike, t, r, sigma = 100.0, 105.0, 0.25, 0.065, 0.2
    call = BlackScholes.price(spot, strike, t, r, sigma, "CE")
    put = BlackScholes.price(spot, strike, t, r, sigma, "PE")
    parity = call + strike * __import__("math").exp(-r * t) - spot
    assert abs(parity - put) < 0.01


def test_implied_volatility_roundtrip():
    spot, strike, t, r, sigma = 100.0, 100.0, 0.5, 0.065, 0.25
    price = BlackScholes.price(spot, strike, t, r, sigma, "CE")
    iv = BlackScholes.implied_volatility(price, spot, strike, t, r, "CE")
    assert abs(iv - sigma) < 1e-3


def test_greeks_call_vs_put():
    g = BlackScholes.greeks(100, 100, 0.5, 0.065, 0.2, "CE")
    assert 0 < g.delta < 1
    assert g.gamma > 0
    assert g.vega > 0
    assert g.rho > 0
    pg = BlackScholes.greeks(100, 100, 0.5, 0.065, 0.2, "PE")
    assert pg.delta < 0  # put delta is negative


def test_option_intrinsic_extrinsic():
    opt = Option("NIFTY 24500 CE", strike=24500, expiry=date.today(), option_type="CE",
                 underlying_symbol="NIFTY", lot_size=75)
    opt._quote = opt._quote.with_update(ltp=150.0)
    assert opt.intrinsic_value(24600) == 100.0
    assert opt.extrinsic_value(24600) == 50.0
    assert opt.moneyness(24600) == "ITM"
    assert opt.moneyness(24000) == "OTM"


def test_option_black_scholes_and_iv():
    opt = Option("NIFTY 24500 CE", strike=24500, expiry=date.today(), option_type="CE",
                 underlying_symbol="NIFTY")
    price = opt.black_scholes(spot=24600, sigma=0.15)
    assert price > 0
    iv = opt.implied_volatility(price, spot=24600)
    assert abs(iv - 0.15) < 1e-2


def test_option_set_greeks():
    opt = Option("NIFTY 24500 CE", strike=24500, expiry=date.today(), option_type="CE",
                 underlying_symbol="NIFTY")
    opt.set_greeks(Greeks(delta=0.55, gamma=0.001, theta=-0.2, vega=0.1, rho=0.01, iv=0.18))
    assert opt.delta == 0.55
    assert opt.iv == 0.18
    assert opt.greeks.delta == 0.55


def test_option_pnl():
    opt = Option("NIFTY 24500 CE", strike=24500, expiry=date.today(), option_type="CE",
                 underlying_symbol="NIFTY", lot_size=75)
    opt._quote = opt._quote.with_update(ltp=200.0)
    assert opt.pnl(buy_price=150.0) == 50.0 * 75


def test_chain_from_dhan_df():
    underlying = Index("NIFTY")
    rows = []
    for strike in (24500, 24550, 24600):
        for leg in ("CE", "PE"):
            rows.append({
                "Strike Price": strike,
                f"{leg} LTP": 50.0, f"{leg} OI": 10000 + strike, f"{leg} Volume": 500,
                f"{leg} IV": 0.15, f"{leg} Delta": 0.5, f"{leg} Gamma": 0.001,
                f"{leg} Theta": -0.2, f"{leg} Vega": 0.1,
            })
    df = pd.DataFrame(rows)
    chain = _chain_from_dhan_df(underlying, df, atm=24550)
    assert len(chain) == 6
    assert len(chain.calls) == 3
    assert len(chain.puts) == 3
    assert chain.atm.strike == 24550
    assert chain[24550.0] is not None
    # domain itself must stay broker-agnostic: no from_dhan_df on the chain
    assert not hasattr(OptionChain, "from_dhan_df")


def test_chain_views_and_analytics():
    broker = PaperBroker()
    underlying = Index("NIFTY", broker=broker)
    underlying._quote = underlying._quote.with_update(ltp=24550.0)
    chain = broker.get_option_chain(underlying, num_strikes=11)
    assert chain.atm is not None
    assert chain.atm_strike is not None
    assert len(chain.expiries()) == 1
    assert chain.nearest_expiry is not None
    assert chain.pcr() > 0
    assert chain.max_pain() > 0
    iv = chain.iv_surface()
    assert "iv" in iv.columns
    g = chain.greeks_table()
    assert "delta" in g.columns
    assert chain[chain.atm_strike] is not None


def test_chain_getitem_missing():
    broker = PaperBroker()
    underlying = Index("NIFTY", broker=broker)
    chain = broker.get_option_chain(underlying, num_strikes=5)
    with pytest.raises(KeyError):
        chain[999999]


def test_chain_subscribe():
    broker = PaperBroker()
    underlying = Index("NIFTY", broker=broker)
    chain = broker.get_option_chain(underlying, num_strikes=5)
    chain.subscribe()
    assert all(o.stream.is_live for o in chain)
