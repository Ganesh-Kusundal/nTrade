"""Indian-market statutory cost model (H6)."""

from __future__ import annotations

import pytest

from ntrade.execution.costs import IndianStatutoryCosts


def test_stt_equity_delivery_sell():
    costs = IndianStatutoryCosts(product="equity", delivery=True, brokerage=0.0)
    # 0.1% STT on sell
    assert costs.stt(100_000.0, "SELL") == pytest.approx(100.0)


def test_stt_equity_delivery_buy():
    costs = IndianStatutoryCosts(product="equity", delivery=True, brokerage=0.0)
    # 0.025% STT on delivery buy
    assert costs.stt(100_000.0, "BUY") == pytest.approx(25.0)


def test_stt_fno_sell_higher():
    costs = IndianStatutoryCosts(product="futures", delivery=False, brokerage=0.0)
    # 0.125% on F&O sell
    assert costs.stt(100_000.0, "SELL") == pytest.approx(125.0)


def test_stamp_duty_buy_only():
    costs = IndianStatutoryCosts(product="equity", delivery=True, brokerage=0.0)
    assert costs.stamp(100_000.0, "BUY") == pytest.approx(3.0)   # 0.003%
    assert costs.stamp(100_000.0, "SELL") == 0.0


def test_gst_on_brokerage_and_charges():
    costs = IndianStatutoryCosts(product="equity", delivery=True, brokerage=20.0)
    notional = 100_000.0
    base = 20.0 + costs.exchange_charge(notional) + costs.sebi(notional)
    assert costs.gst(notional) == pytest.approx(base * 0.18)


def test_total_cost_positive_and_known():
    costs = IndianStatutoryCosts(product="options", brokerage=20.0)
    total = costs.total_cost(100_000.0, "SELL")
    assert total > 0.0
    # options sell: STT 125 + exchange 0.0503%*100k=50.3 + sebi 0.1 + stamp 0
    #             + gst(20 + 50.3 + 0.1)*0.18
    expected = 125.0 + 50.3 + 0.1 + 0.0 + (20.0 + 50.3 + 0.1) * 0.18
    assert total == pytest.approx(expected, rel=1e-6)


def test_custom_rates_override():
    costs = IndianStatutoryCosts(
        product="equity", delivery=True, brokerage=0.0,
        stt={"equity_delivery_sell": 0.002},  # custom 0.2%
    )
    assert costs.stt(10_000.0, "SELL") == pytest.approx(20.0)


def test_intraday_buy_has_no_stt():
    """India charges STT only on the sell side for intraday equity."""
    costs = IndianStatutoryCosts(product="equity", delivery=False, brokerage=0.0)
    assert costs.stt(100_000.0, "BUY") == 0.0
    # intraday sell still attracts 0.025%
    assert costs.stt(100_000.0, "SELL") == pytest.approx(25.0)


def test_futures_exchange_charge():
    costs = IndianStatutoryCosts(product="futures", brokerage=0.0)
    # 0.00173% on notional
    assert costs.exchange_charge(100_000.0) == pytest.approx(1.73)


def test_custom_exchange_charge_override():
    costs = IndianStatutoryCosts(
        product="equity", brokerage=0.0,
        exchange_charge={"equity": 0.00005},  # custom 0.005%
    )
    assert costs.exchange_charge(10_000.0) == pytest.approx(0.5)
