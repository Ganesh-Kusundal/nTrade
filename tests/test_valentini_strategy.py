"""ValentiniScalper tests — Triple-A state machine through the kernel flow.

TDD: written before the strategy — defines the contract for the
``ValentiniScalper`` in ``ntrade/engines/strategies.py``.

The model (approximated, no Dhan trade tape): absorption bar -> accumulate
near value -> aggressive signal when price is above VWAP (long) / below
VWAP (short). Entries carry SL/TP; exits are managed per candle.
"""

from datetime import datetime, timedelta

from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategies import ValentiniScalper
from ntrade.events.market import CandleClosedEvent, QuoteEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel

_TS = datetime(2026, 8, 3, 10, 0)   # within session 09:15-15:25
_NIFTY = "NIFTY"


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=1_000_000.0)
    k.register(Equity(_NIFTY))
    return k


def _candle(k, i, *, close=None, open_=None, high=None, low=None, volume=100,
            ts=None):
    """Publish one 1m closed candle; OHLC default to a small bullish bar."""
    c = close if close is not None else 100.0 + i * 0.5
    o = open_ if open_ is not None else c - 0.5
    h = high if high is not None else max(c, o) + 0.5
    lo = low if low is not None else min(c, o) - 0.5
    ts = ts or (_TS + timedelta(minutes=i))
    # Zero-parity with BacktestSimulator's pre-candle quote: publish the bar
    # as a QuoteEvent first so instrument._quote.ltp is set — MARKET orders
    # are rejected by SimulatedExecution otherwise ("no market price
    # available"). No TickEvent: CandleEngine.on_tick has no replay-mode guard
    # and would emit a duplicate tick-built candle.
    k.bus.publish(QuoteEvent(
        symbol=_NIFTY, exchange="NSE", ltp=c, bid=0.0, ask=0.0,
        open=o, high=h, low=lo, volume=volume, ts=ts,
    ))
    k.bus.publish(CandleClosedEvent(
        symbol=_NIFTY, exchange="NSE", timeframe="1m",
        open=o, high=h, low=lo, close=c, volume=volume, ts=ts,
    ))


def _uptrend_bars(k, n=40, start=100.0, step=0.5, volume=100):
    """Publish n rising candles to build warmup + range bars + profile."""
    for i in range(n):
        _candle(k, i, close=start + i * step, volume=volume)


def _absorption_bar(k, i, at, volume=1500, span=0.05, ts=None):
    """Publish a high-volume compressed candle at price ``at`` (absorption)."""
    _candle(k, i, close=at, open_=at - 0.02, high=at + span,
            low=at - span, volume=volume, ts=ts)


def _fills(k):
    from ntrade.events.order import OrderFilledEvent
    return [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]


def _signals(k):
    from ntrade.events.risk import SignalGeneratedEvent
    return [e for e in k.bus.history if isinstance(e, SignalGeneratedEvent)]


# ------------------------------------------------------------------ phase

def test_phase_waits_without_absorption(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _uptrend_bars(k, 30)
    assert strat.phase == "waiting"


def _fixed_profile(monkeypatch, val, poc, vah, step=4.0):
    """Force the strategy's volume-profile analysis to a known POC/VAH/VAL
    (white-box) so the Triple-A location/SL/TP/balance rules are tested in
    isolation from profile construction."""
    from ntrade.domain.analytics.volume_profile import VolumeProfile, VPLevel
    prof = VolumeProfile(
        levels=tuple(
            VPLevel(price=p, volume=1.0) for p in (val, poc, vah)
        ),
        poc=poc, vah=vah, val=val, step=step,
    )
    monkeypatch.setattr(
        "ntrade.engines.strategies.build_volume_profile",
        lambda *a, **kw: prof,
    )
    return prof


def test_phase_reaches_absorbing_when_absorption_at_value_edge(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)                       # base
    _absorption_bar(k, 30, at=110.0)           # BUY absorption at VAL
    assert strat.phase == "absorbing"


def test_absorption_away_from_value_edge_does_not_arm(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    # Absorption mid-value (POC) — guide requires it at VAL/VAH, so it
    # must NOT arm the machine.
    _absorption_bar(k, 30, at=130.0)
    assert strat.phase == "waiting"


def test_triple_a_long_signal_emits_buy(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)                       # base
    _absorption_bar(k, 30, at=110.0)           # BUY absorption at VAL
    _candle(k, 31, close=118.0)                # consolidate at POC
    _candle(k, 32, close=122.0)                # break above VWAP + POC -> signal
    fills = _fills(k)
    assert any(f.side == "BUY" for f in fills)


def test_accumulation_requires_price_near_poc(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)           # absorbing at VAL
    _candle(k, 31, close=130.0)                # ran far from POC -> not accumulating
    assert strat.phase == "absorbing"


def test_vwap_filter_blocks_signal_when_price_below_vwap(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    # Downtrend so the last price sits BELOW VWAP: a BUY absorption must
    # NOT produce a LONG signal (price must be > VWAP).
    for i in range(35):
        _candle(k, i, close=120.0 - i * 0.5, volume=100)
    _absorption_bar(k, 35, at=111.0)           # BUY absorption at VAL
    _candle(k, 36, close=101.8)
    _candle(k, 37, close=101.6)
    assert not any(f.side == "BUY" for f in _fills(k))


def test_balance_inside_value_area_blocks_signal(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    # Close consolidates exactly at the POC (mid-value inside VA) -> balance,
    # so even a VWAP breakout must NOT fire.
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)                # at POC
    _candle(k, 32, close=115.2)                # small break, still mid-value
    assert not any(f.side == "BUY" for f in _fills(k))


def test_cvd_proxy_disagreement_blocks_signal(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, require_cvd=True)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    # Force the OHLCV CVD proxy negative (bearish) even though price breaks
    # above VWAP -> aggression leg must wait.
    import pandas as pd
    from ntrade.domain.analytics.order_flow import cvd_from_ohlcv
    monkeypatch.setattr(
        "ntrade.engines.strategies.cvd_from_ohlcv",
        lambda df: pd.Series([-5.0, -5.0, -5.0], index=df.index[-3:]),
    )
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)
    assert not any(f.side == "BUY" for f in _fills(k))
    assert strat.phase == "accumulating"        # blocked by CVD, keeps setup


def test_long_stop_pinned_below_val(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)
    sig = [s for s in _signals(k) if s.metadata.get("phase") == "signal"]
    assert sig
    # SL = VAL - step = 110 - 4 = 106; signal reports val_vah source.
    assert sig[0].metadata['sl'] == 106.0
    assert sig[0].metadata.get("sl_source") == "val_vah"


def test_prior_session_poc_used_as_target(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    # Prior-session POC (aggressive target) sits well above entry.
    strat._prior_poc = 150.0
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)
    sig = [s for s in _signals(k) if s.metadata.get("phase") == "signal"]
    assert sig
    assert sig[0].metadata['tp'] == 150.0                       # prior POC target
    assert sig[0].metadata.get("target") == "prior_poc"


def test_no_signal_without_absorption(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _uptrend_bars(k, 40)                       # no absorption anywhere
    assert _fills(k) == []


def test_no_double_entry_while_position_open(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # signal -> BUY (long)
    assert len(_fills(k)) == 1
    # keep feeding strong candles — no second entry while long
    for i in range(33, 40):
        _candle(k, i, close=116.0 + i)
    assert len([f for f in _fills(k) if f.side == "BUY"]) == 1


# ------------------------------------------------------------------ exits

def test_stop_loss_exit_emits_sell(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # BUY entry
    assert len(_fills(k)) == 1
    entry = _fills(k)[0]
    # A sharp drop through the stop (VAL - step = 107) -> SELL exit.
    _candle(k, 33, close=106.0, open_=115.5, high=115.6, low=105.0, volume=500)
    sells = [f for f in _fills(k) if f.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].quantity == entry.quantity


def test_stop_wins_when_bar_hits_both_stop_and_target(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)           # sl = 110 - 4 = 106
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # BUY entry; tp = 130 (prior_poc) or R-fallback
    assert len(_fills(k)) == 1
    # One candle trades through BOTH the stop (107) and a high target:
    # the stop is the protective order and must win (conservative fill).
    _candle(k, 33, close=112.0, open_=116.0, high=126.0, low=105.0)
    sells = [e for e in _signals(k) if e.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].metadata.get("exit_reason") == "stop"
    assert len([f for f in _fills(k) if f.side == "SELL"]) == 1  # no double exit


def test_hard_session_close_exits_open_position(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             session_end="15:25", tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # BUY entry
    assert len(_fills(k)) == 1
    # A benign in-session candle then a candle past the session gate: the
    # methodology mandates a hard market close — never hold overnight.
    _candle(k, 33, close=116.0, ts=datetime(2026, 8, 3, 15, 26))
    sells = [e for e in _signals(k) if e.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].metadata.get("exit_reason") == "session_close"
    assert strat.phase == "waiting"


def test_extended_chase_beyond_vwap_band_waits_for_pullback(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5)
    k.register_strategy(strat)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=115.0)
    _candle(k, 31, close=115.4)
    # Push far beyond VWAP +2σ (upper ≈ 120.3 on this window): the setup
    # must NOT chase an extended move — it stays accumulating.
    _candle(k, 32, close=121.0, open_=120.0, high=121.5, low=119.5)
    assert _fills(k) == []
    assert strat.phase == "accumulating"
    # Pullback inside the band -> the same setup now fires.
    _candle(k, 33, close=118.0)
    assert any(f.side == "BUY" for f in _fills(k))


def test_strategy_can_reenter_after_rejected_entry(monkeypatch):
    k = _kernel()
    k.risk_engine.max_quantity = 0            # reject the first entry
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)               # signal -> rejected
    assert _fills(k) == []
    assert strat._active is None
    # Risk re-enabled: a fresh setup must be able to re-enter. Regression:
    # the stale _pending used to permanently lock the strategy out.
    k.risk_engine.max_quantity = None
    _absorption_bar(k, 33, at=111.0, volume=2000)
    _candle(k, 34, close=115.0)
    _candle(k, 35, close=122.0)
    assert any(f.side == "BUY" for f in _fills(k))


def test_rejected_entry_does_not_arm_phantom_position(monkeypatch):
    k = _kernel()
    # RiskEngine rejects any positive quantity -> every entry signal is
    # denied. The entry must NOT arm _active; no phantom exit may follow.
    k.risk_engine.max_quantity = 0
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # signal -> rejected by risk
    assert strat._active is None
    assert _fills(k) == []                     # entry never filled
    # Keep feeding candles: no SELL exit may appear for a phantom position.
    for i in range(33, 38):
        _candle(k, i, close=116.0 + i)
    assert not any(f.side == "SELL" for f in _fills(k))
    assert not any(e.side == "SELL" for e in _signals(k))


def test_absorbing_phase_expires_when_price_runs_away(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)           # absorbing at VAL
    assert strat.phase == "absorbing"
    # Price runs away > 2*step for abs_lookback*3 bars (15) -> setup expires
    # back to waiting instead of hanging in absorbing forever.
    for i in range(31, 47):
        _candle(k, i, close=120.0 + (i - 31) * 5.0)
    assert strat.phase == "waiting"
    # A fresh absorption can now re-arm the machine.
    _absorption_bar(k, 47, at=111.0)
    assert strat.phase == "absorbing"


def test_session_gate_blocks_outside_hours(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             session_end="15:25", tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)                       # in-session candles
    # Absorption at 16:00 — after the session gate -> no trade.
    _absorption_bar(k, 30, at=111.0, ts=datetime(2026, 8, 3, 16, 0))
    _candle(k, 31, close=115.0, ts=datetime(2026, 8, 3, 16, 1))
    _candle(k, 32, close=116.0, ts=datetime(2026, 8, 3, 16, 2))
    assert _fills(k) == []


def test_session_gate_handles_utc_timestamps():
    """A UTC timestamp must be normalized to IST before the session gate.

    Live feed data arrives in UTC+5:30 (IST). 09:45 UTC = 15:15 IST (in
    session); 10:00 UTC = 15:30 IST (out of session). Naive timestamps
    are assumed IST (backward-compatible)."""
    from datetime import timezone, timedelta

    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             session_start="09:15", session_end="15:25",
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False)
    k.register_strategy(strat)

    utc = timezone.utc
    # 09:45 UTC = 15:15 IST → in session
    in_ts = datetime(2026, 8, 3, 9, 45, tzinfo=utc)
    assert strat._in_session(in_ts) is True

    # 10:00 UTC = 15:30 IST → out of session
    out_ts = datetime(2026, 8, 3, 10, 0, tzinfo=utc)
    assert strat._in_session(out_ts) is False

    # Naive timestamps are assumed IST (backward compat)
    naive_in = datetime(2026, 8, 3, 10, 0)  # 10:00 IST → in session
    assert strat._in_session(naive_in) is True


# ------------------------------------------------------------------ sizing

def _depth(k, bids, asks):
    from ntrade.events.market import DepthEvent
    k.bus.publish(DepthEvent(
        symbol=_NIFTY, exchange="NSE", bids=bids, asks=asks,
        ts=_TS + timedelta(minutes=33)))


def test_depth_imbalance_filter_blocks_without_buy_pressure(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False,
                             depth_imbalance_min=0.3)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    # SELL-heavy book: imbalance = (100-900)/1000 = -0.8 < 0.3 -> BUY blocked.
    _depth(k, bids=((100.0, 100, 1),), asks=((100.1, 900, 1),))
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)
    assert _fills(k) == []
    assert strat.phase == "accumulating"   # still waiting for buy pressure
    # Flip to a BUY-heavy book (imbalance +0.8) -> the same setup now fires.
    _depth(k, bids=((100.0, 900, 1),), asks=((100.1, 100, 1),))
    _candle(k, 33, close=122.0)
    assert any(f.side == "BUY" for f in _fills(k))


def test_depth_filter_skipped_without_live_depth(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False,
                             depth_imbalance_min=0.3)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)       # no DepthEvent ever published
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)
    # Zero-parity: backtest/paper have an empty book -> filter is skipped.
    assert any(f.side == "BUY" for f in _fills(k))


def test_position_sized_from_risk_budget(monkeypatch):
    k = _kernel()
    strat = ValentiniScalper(symbol=_NIFTY, range_size=4.0, warmup=15,
                             tp_multiplier=2.0, min_rr=1.5, fade_extended=False,
                             risk_per_trade_pct=0.5, lot_size=1)
    k.register_strategy(strat)
    _fixed_profile(monkeypatch, val=110.0, poc=118.0, vah=126.0)
    _uptrend_bars(k, 30)
    _absorption_bar(k, 30, at=110.0)
    _candle(k, 31, close=118.0)
    _candle(k, 32, close=122.0)                # BUY entry
    buys = [f for f in _fills(k) if f.side == "BUY"]
    assert buys
    # 0.5% of 1,000,000 = 5,000 risk / ~1.5-2.0 pt stop -> thousands of qty,
    # floored to lot size (>= 1). Sanity: within [1, risk/stop].
    qty = buys[0].quantity
    assert qty >= 1
    assert qty <= 100_000
