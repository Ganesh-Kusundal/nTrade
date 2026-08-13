"""MorningVAHVAL tests — the Mukul Chowdhury 2-minute morning scalper.

TDD: written alongside the strategy in ``ntrade/engines/morning_vah_val.py``.

Covers: prior-day bias, the 09:15-09:30 FRVP freeze, the VAL-fake-break long /
VAH-rejection short entries, 100% vs 50% risk sizing, T1 partial + breakeven +
candle-by-candle trail, the 11:00 hard close, the stop, and the session-gate
force close on rollover.
"""

from datetime import datetime, timedelta

from ntrade.domain.instruments.cash import Equity
from ntrade.engines.morning_vah_val import MorningVAHVAL
from ntrade.events.market import CandleClosedEvent, QuoteEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel

_NIFTY = "NIFTY"

D1 = datetime(2026, 8, 3)   # Monday — prior day
D2 = datetime(2026, 8, 4)   # Tuesday — trade day
D3 = datetime(2026, 8, 5)   # Wednesday — rollover check


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=1_000_000.0)
    k.register(Equity(_NIFTY))
    return k


def _candle(k, ts, *, open_, high, low, close, volume=100):
    """Publish one 1m closed candle (Quote first so MARKET orders can fill)."""
    k.bus.publish(QuoteEvent(
        symbol=_NIFTY, exchange="NSE", ltp=close, bid=0.0, ask=0.0,
        open=open_, high=high, low=low, volume=volume, ts=ts,
    ))
    k.bus.publish(CandleClosedEvent(
        symbol=_NIFTY, exchange="NSE", timeframe="1m",
        open=open_, high=high, low=low, close=close, volume=volume, ts=ts,
    ))


def _session_day(k, day, open_px, close_px):
    """A full 09:15-15:29 session drifting open_px → close_px (1m bars)."""
    n = 375
    step = (close_px - open_px) / (n - 1)
    start = day.replace(hour=9, minute=15)
    for i in range(n):
        c = open_px + step * i
        o = c - step * 0.5
        _candle(k, ts=start + timedelta(minutes=i), open_=o,
                high=max(o, c) + 0.5, low=min(o, c) - 0.5, close=c)


def _profile_window(k, day, fake_dip=True):
    """09:15-09:29 1m bars producing a frozen FRVP of VAL ≈ 99, VAH ≈ 101.

    Balanced volume around the 99 POC (5 bars @ 99.5) and the 101 area
    (5 bars @ 101.5 + 3 above), so the 68% value area spans 99 → 101.
    ``fake_dip`` makes the final two minutes (09:28-09:29 — the last 2m
    bucket before the entry window) dip below VAL: the fake breakdown.
    Otherwise they rally above VAH: the short-side test.
    """
    start = day.replace(hour=9, minute=15)
    for i in range(10):  # minutes 09:15..09:24 — POC at 99.0 / upper at 101.0
        c = 99.5 if i % 2 == 0 else 101.5
        o = c - 1.5
        _candle(k, ts=start + timedelta(minutes=i), open_=o,
                high=c + 0.5, low=c - 1.5, close=c)
    # 09:25..09:27 — thicken the region above VAH so VA extends upward.
    _candle(k, ts=start + timedelta(minutes=10), open_=102.0, high=103.0,
            low=101.5, close=102.5, volume=50)
    _candle(k, ts=start + timedelta(minutes=11), open_=102.5, high=103.2,
            low=102.0, close=103.0, volume=50)
    _candle(k, ts=start + timedelta(minutes=12), open_=103.0, high=103.2,
            low=101.5, close=102.0, volume=50)
    ts13 = start + timedelta(minutes=13)      # 09:28
    ts14 = start + timedelta(minutes=14)      # 09:29
    if fake_dip:
        _candle(k, ts=ts13, open_=99.0, high=98.5, low=97.3, close=97.6)
        _candle(k, ts=ts14, open_=97.6, high=98.2, low=97.2, close=97.7)
    else:
        _candle(k, ts=ts13, open_=99.0, high=102.5, low=98.8, close=101.8)
        _candle(k, ts=ts14, open_=101.8, high=102.7, low=101.0, close=102.2)


def _reversal_long(k, day):
    """09:30-09:31 reversal closing back above VAL (long entry trigger)."""
    _candle(k, ts=day.replace(hour=9, minute=30),
            open_=97.7, high=98.8, low=97.4, close=98.4)
    _candle(k, ts=day.replace(hour=9, minute=31),
            open_=98.4, high=99.9, low=98.2, close=99.6)


def _reversal_short(k, day):
    """09:30-09:31 rejection closing back below VAH (short entry trigger)."""
    _candle(k, ts=day.replace(hour=9, minute=30),
            open_=101.6, high=102.2, low=101.0, close=101.2)
    _candle(k, ts=day.replace(hour=9, minute=31),
            open_=101.2, high=101.4, low=99.6, close=99.4)


def _rally_window(k, day, to):
    """Benign 09:32..``to`` 1m bars that never touch a long's stop."""
    base = day.replace(hour=9, minute=32)
    for i in range(to - 572 + 1):
        c = 99.7 + i * 0.01
        _candle(k, ts=base + timedelta(minutes=i), open_=c - 0.05,
                high=c + 0.1, low=c - 0.2, close=c)


def _fills(k):
    from ntrade.events.order import OrderFilledEvent
    return [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]


def _signals(k):
    from ntrade.events.risk import SignalGeneratedEvent
    return [e for e in k.bus.history if isinstance(e, SignalGeneratedEvent)]


def _upday(k):
    _session_day(k, D1, 100.0, 130.0)


def _downday(k):
    _session_day(k, D1, 130.0, 100.0)


def _long_entry(k):
    """Full long setup through the 09:32 candle (entry bucket completes)."""
    _upday(k)
    _profile_window(k, D2)
    _reversal_long(k, D2)
    _candle(k, ts=D2.replace(hour=9, minute=32), open_=99.6, high=99.7,
            low=99.4, close=99.6)


# ------------------------------------------------------------ bias + freeze

def test_day_one_has_no_bias_and_no_trades():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _upday(k)
    assert strat.bias is None          # day one: no prior context
    assert strat.profile_ready         # the FRVP still freezes
    assert _fills(k) == []


def test_prior_day_up_arms_long_bias():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _upday(k)
    _candle(k, ts=D2.replace(hour=9, minute=15), open_=99.0, high=99.5,
            low=98.5, close=99.2)      # first candle of the trade day
    assert strat.bias == "UP"


def test_prior_day_down_arms_short_bias():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _downday(k)
    _candle(k, ts=D2.replace(hour=9, minute=15), open_=99.0, high=99.5,
            low=98.5, close=99.2)
    assert strat.bias == "DOWN"


def test_sideways_prior_day_blocks_trades():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _session_day(k, D1, 100.0, 100.1)  # flat day → SIDEWAYS (< 0.15% drift)
    _candle(k, ts=D2.replace(hour=9, minute=15), open_=99.0, high=99.5,
            low=98.5, close=99.2)      # rollover computes the bias
    assert strat.bias == "SIDEWAYS"
    _profile_window(k, D2)
    _reversal_long(k, D2)
    _candle(k, ts=D2.replace(hour=9, minute=32), open_=99.6, high=99.7,
            low=99.4, close=99.6)
    assert _fills(k) == []


def test_profile_frozen_from_first_15_minutes():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _upday(k)
    _profile_window(k, D2)
    _reversal_long(k, D2)
    assert strat.profile_ready
    assert 0 < strat.val < strat.vah


def test_no_entry_before_profile_freezes():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _upday(k)
    # Only 09:15-09:29 bars — the FRVP window never closes (no 09:30 bar),
    # so the setup must stay silent even though a dip/reversal pattern exists.
    _profile_window(k, D2)
    assert not strat.profile_ready
    assert _fills(k) == []


# ------------------------------------------------------------ long entry

def test_long_fake_break_below_val_emits_buy():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, risk_per_trade_pct=0.5, lot_size=1)
    k.register_strategy(strat)
    _long_entry(k)

    buys = [f for f in _fills(k) if f.side == "BUY"]
    assert len(buys) == 1
    sig = [s for s in _signals(k) if s.metadata.get("phase") == "signal"]
    assert sig and sig[0].side == "BUY"
    assert sig[0].metadata["bias"] == "UP"
    # SL at/below the value low (the dip), entry inside value, TP = VAH.
    sl, entry = sig[0].metadata["sl"], buys[0].fill_price
    assert sl <= sig[0].metadata["val"] < entry < sig[0].metadata["tp"] <= sig[0].metadata["vah"]
    # Not at the EMA cluster yet (< 20 bars) → half sizing = 50% of risk.
    assert sig[0].metadata["sizing"] == "half"


def test_full_sizing_when_reversal_at_ema_cluster(monkeypatch):
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, risk_per_trade_pct=0.5, lot_size=1)
    strat._ema_cluster = lambda close: True   # white-box: reversal at cluster
    k.register_strategy(strat)
    _long_entry(k)
    sig = [s for s in _signals(k) if s.metadata.get("phase") == "signal"]
    assert sig and sig[0].metadata["sizing"] == "full"
    # 0.5% of 1,000,000 = 5,000 risk / (entry - sl) → floored to lot size.
    risk_per_unit = 5000.0 / (99.6 - sig[0].metadata["sl"])
    assert sig[0].quantity == int(risk_per_unit)


def test_half_sizing_away_from_ema_cluster():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, risk_per_trade_pct=0.5, lot_size=1)
    k.register_strategy(strat)
    _long_entry(k)
    sig = [s for s in _signals(k) if s.metadata.get("phase") == "signal"]
    assert sig and sig[0].metadata["sizing"] == "half"
    # Half sizing = 2,500 risk / (entry - sl) → floored to lot size.
    risk_per_unit = 2500.0 / (99.6 - sig[0].metadata["sl"])
    assert sig[0].quantity == int(risk_per_unit)


def test_short_vah_rejection_emits_sell():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, risk_per_trade_pct=0.5, lot_size=1)
    k.register_strategy(strat)
    _downday(k)
    _profile_window(k, D2, fake_dip=False)   # 09:28-09:29 rally to VAH
    _reversal_short(k, D2)
    _candle(k, ts=D2.replace(hour=9, minute=32), open_=99.4, high=99.5,
            low=99.2, close=99.4)

    sells = [f for f in _fills(k) if f.side == "SELL"]
    assert len(sells) == 1
    sig = [s for s in _signals(k) if s.metadata.get("phase") == "signal"]
    assert sig and sig[0].side == "SELL"
    assert sig[0].metadata["bias"] == "DOWN"
    # SL above the signal-bar/prior-bar high; TP = VAL.
    assert sig[0].metadata["sl"] > sig[0].metadata["vah"] > sig[0].metadata["tp"] >= sig[0].metadata["val"]


def test_downtrend_does_not_fire_long_even_with_dip():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _downday(k)                              # DOWN bias → longs forbidden
    _profile_window(k, D2)
    _reversal_long(k, D2)
    _candle(k, ts=D2.replace(hour=9, minute=32), open_=99.6, high=99.7,
            low=99.4, close=99.6)
    assert _fills(k) == []


def test_no_double_entry_while_position_open():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _long_entry(k)                           # BUY entry
    assert len([f for f in _fills(k) if f.side == "BUY"]) == 1
    _rally_window(k, D2, 600)                # strong continuation
    assert len([f for f in _fills(k) if f.side == "BUY"]) == 1


# ------------------------------------------------------------ exits

def test_stop_loss_exits_long():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _long_entry(k)                           # BUY entry (sl ≈ 97.3)
    assert len(_fills(k)) == 1
    # Benign 09:33 completes the 09:32-09:33 bucket, then the 09:34-09:35
    # bucket craters through the stop — completed by the 09:36 candle.
    _candle(k, ts=D2.replace(hour=9, minute=33), open_=99.7, high=99.8,
            low=98.9, close=99.2)
    _candle(k, ts=D2.replace(hour=9, minute=34), open_=99.2, high=99.3,
            low=96.5, close=96.9)
    _candle(k, ts=D2.replace(hour=9, minute=35), open_=96.9, high=97.0,
            low=96.3, close=96.6)
    _candle(k, ts=D2.replace(hour=9, minute=36), open_=96.6, high=96.7,
            low=95.8, close=96.0)
    sells = [s for s in _signals(k) if s.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].metadata.get("exit_reason") == "stop"


def test_t1_partial_books_half_and_moves_to_breakeven():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1)
    k.register_strategy(strat)
    _long_entry(k)                           # BUY entry
    entry = [f for f in _fills(k) if f.side == "BUY"][0]
    # Rally to VAH (~101) in the 09:32-09:33 bucket.
    _candle(k, ts=D2.replace(hour=9, minute=33), open_=99.7, high=103.0,
            low=99.6, close=102.6)
    _candle(k, ts=D2.replace(hour=9, minute=34), open_=102.6, high=102.8,
            low=102.0, close=102.4)

    partial = [s for s in _signals(k) if s.metadata.get("partial")]
    assert len(partial) == 1
    assert partial[0].metadata.get("exit_reason") == "partial_target"
    # Half of the entry quantity is booked at T1; the rest goes to breakeven.
    assert partial[0].quantity == entry.quantity // 2
    assert strat._active is not None
    assert strat._active["qty_left"] == entry.quantity - partial[0].quantity
    assert strat._active["phase"] == "be"
    assert strat._active["sl"] == strat._active["entry"]   # cost-to-cost
    assert strat._active["tp"] is None


def test_breakeven_stop_exits_runner_after_t1():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1)
    k.register_strategy(strat)
    _long_entry(k)                           # BUY entry @ ~99.6
    _candle(k, ts=D2.replace(hour=9, minute=33), open_=99.7, high=103.0,
            low=99.6, close=102.6)           # T1 partial hit (be, sl=entry)
    _candle(k, ts=D2.replace(hour=9, minute=34), open_=102.6, high=102.8,
            low=102.0, close=102.4)
    assert strat._active["phase"] == "be"
    # A drift back below the breakeven entry trips the cost-to-cost stop.
    _candle(k, ts=D2.replace(hour=9, minute=35), open_=102.4, high=102.6,
            low=99.4, close=99.6)
    _candle(k, ts=D2.replace(hour=9, minute=36), open_=99.6, high=99.7,
            low=99.2, close=99.4)
    exits = [s for s in _signals(k) if s.side == "SELL"
             and s.metadata.get("exit_reason") == "stop"]
    assert exits
    assert strat._active is None


def test_hard_close_at_1100_exits_runner():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1)
    k.register_strategy(strat)
    _long_entry(k)                           # BUY entry
    # Never reaches VAH; benign drift until the 11:00 bucket completes.
    _rally_window(k, D2, 660)                # 09:32..11:00
    _candle(k, ts=D2.replace(hour=11, minute=2), open_=99.7, high=99.8,
            low=99.5, close=99.7)            # completes the 11:00-11:01 bucket
    closes = [s for s in _signals(k) if s.metadata.get("exit_reason") == "time_close"]
    assert len(closes) == 1
    assert strat._active is None


def test_session_rollover_force_closes_open_position():
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1)
    k.register_strategy(strat)
    _long_entry(k)                           # BUY entry — never closed
    assert len(_fills(k)) == 1
    # Next morning: the rollover must force-close the overnight position.
    _candle(k, ts=D3.replace(hour=9, minute=15), open_=99.0, high=99.5,
            low=98.5, close=99.2)
    closes = [s for s in _signals(k) if s.metadata.get("exit_reason") == "session_close"]
    assert len(closes) == 1
    assert strat._active is None


def test_rejected_entry_does_not_arm_phantom_position():
    k = _kernel()
    k.risk_engine.max_quantity = 0           # every signal rejected
    strat = MorningVAHVAL(symbol=_NIFTY)
    k.register_strategy(strat)
    _long_entry(k)                           # entry signal → rejected
    assert _fills(k) == []
    assert strat._active is None             # no phantom position
    _rally_window(k, D2, 600)
    assert not any(s.side == "SELL" for s in _signals(k))  # no phantom exit


# ------------------------------------------------------------ chandelier trail

def test_chandelier_trail_holds_through_small_pullback():
    """Chandelier (day_high − mult×ATR) is wider than the candle ratchet: a
    pullback that trips the per-bar ratchet survives the ATR-based stop.

    A volatile run-up (big ranges → big ATR) followed by a calm pullback:
    the ratchet hugs the previous bar's low (102.0 at the time of the
    pullback) while the chandelier stop sits ~an ATR below the day high
    (~101.8) — the same 1m feed stops the ratchet and holds the chandelier.
    """
    def run(trail_mode):
        k = _kernel()
        strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1, trail_mode=trail_mode,
                              atr_mult=1.0, atr_period=2, book_partial=False)
        k.register_strategy(strat)
        _upday(k)
        _profile_window(k, D2)
        _reversal_long(k, D2)
        # Entry bucket 09:30-31 completes at 09:32; 09:32-33 rallies to VAH
        # (high 104.5) -> T1 -> be phase (sl = entry ≈ 99.6).
        _candle(k, ts=D2.replace(hour=9, minute=32), open_=99.6, high=100.0,
                low=99.5, close=99.8)
        _candle(k, ts=D2.replace(hour=9, minute=33), open_=99.8, high=104.5,
                low=99.6, close=103.0)
        _candle(k, ts=D2.replace(hour=9, minute=34), open_=103.0, high=103.2,
                low=102.0, close=102.5)
        assert strat._active["phase"] == "be"
        # Two continuation buckets near the day high (volatile ranges).
        _candle(k, ts=D2.replace(hour=9, minute=35), open_=102.5, high=104.0,
                low=103.0, close=103.5)
        _candle(k, ts=D2.replace(hour=9, minute=36), open_=103.5, high=103.8,
                low=102.8, close=103.4)
        _candle(k, ts=D2.replace(hour=9, minute=37), open_=103.4, high=104.2,
                low=101.0, close=102.0)
        _candle(k, ts=D2.replace(hour=9, minute=38), open_=102.0, high=102.3,
                low=101.5, close=101.8)
        # Calm pullback: stays above the chandelier stop (~101.8) but dips
        # below the ratchet stop (~102.0).
        _candle(k, ts=D2.replace(hour=9, minute=39), open_=101.8, high=103.2,
                low=102.6, close=102.9)
        _candle(k, ts=D2.replace(hour=9, minute=40), open_=102.9, high=103.0,
                low=102.5, close=102.7)
        return strat, k

    candle_strat, kc = run("candle")
    chand_strat, _ = run("chandelier")
    # The candle ratchet pushed the stop up through the pullback -> stopped.
    assert candle_strat._active is None
    assert any(s.metadata.get("exit_reason") == "stop" for s in _signals(kc))
    # The chandelier held (day_high - 1.0*ATR sits below the pullback's low)
    # and kept ratcheting its stop up on the day high.
    assert chand_strat._active is not None
    assert chand_strat._active["sl"] > chand_strat._active["entry"]


def test_chandelier_ratchets_up_on_new_highs():
    """The chandelier stop must follow new day highs, locking in gains."""
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1, trail_mode="chandelier",
                          atr_mult=1.0, atr_period=2, book_partial=False)
    k.register_strategy(strat)
    _upday(k)
    _profile_window(k, D2)
    _reversal_long(k, D2)
    _candle(k, ts=D2.replace(hour=9, minute=32), open_=99.6, high=103.0,
            low=99.6, close=102.0)
    _candle(k, ts=D2.replace(hour=9, minute=33), open_=102.0, high=102.6,
            low=99.8, close=102.2)   # T1 -> be, sl=entry
    first_sl = strat._active["sl"]
    # New day high: the chandelier stop must ratchet above breakeven once the
    # 09:34-35 bucket completes (its 09:36 candle closes the bucket).
    _candle(k, ts=D2.replace(hour=9, minute=34), open_=102.2, high=105.0,
            low=101.5, close=104.6)
    _candle(k, ts=D2.replace(hour=9, minute=35), open_=104.6, high=104.8,
            low=103.5, close=104.4)
    _candle(k, ts=D2.replace(hour=9, minute=36), open_=104.4, high=104.6,
            low=103.8, close=104.2)
    assert strat._active["sl"] > first_sl, "chandelier must ratchet on new highs"
    assert strat._active["sl"] > strat._active["entry"]


# ------------------------------------------------------------ tuning knobs

def test_require_cluster_blocks_non_cluster_entry():
    """With require_cluster on, the off-cluster (50%-tier) setup must NOT fire."""
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, risk_per_trade_pct=0.5, lot_size=1,
                          require_cluster=True)
    k.register_strategy(strat)
    _long_entry(k)                           # < 20 bars → not at the cluster
    assert _fills(k) == []
    assert strat._active is None
    # With the same feed but cluster forced on, the entry DOES fire (white-box:
    # the knob gates the sizing decision, it doesn't break the setup itself).
    k2 = _kernel()
    strat2 = MorningVAHVAL(symbol=_NIFTY, risk_per_trade_pct=0.5, lot_size=1,
                           require_cluster=True)
    strat2._ema_cluster = lambda close: True
    k2.register_strategy(strat2)
    _long_entry(k2)
    assert len([f for f in _fills(k2) if f.side == "BUY"]) == 1


def test_book_partial_false_rides_full_position_at_breakeven():
    """book_partial=False: T1 moves to breakeven WITHOUT a partial fill — the
    full quantity rides the trail (half the fills → half the cost drag)."""
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1, book_partial=False)
    k.register_strategy(strat)
    _long_entry(k)
    entry = [f for f in _fills(k) if f.side == "BUY"][0]
    # Rally to VAH (~101) in the 09:32-09:33 bucket.
    _candle(k, ts=D2.replace(hour=9, minute=33), open_=99.7, high=103.0,
            low=99.6, close=102.6)
    _candle(k, ts=D2.replace(hour=9, minute=34), open_=102.6, high=102.8,
            low=102.0, close=102.4)
    # No partial fill, no partial signal — T1 just re-arms the runner at BE.
    assert not [s for s in _signals(k) if s.metadata.get("partial")]
    assert strat._active is not None
    assert strat._active["qty_left"] == entry.quantity   # full size intact
    assert strat._active["phase"] == "be"
    assert strat._active["sl"] == strat._active["entry"]
    assert strat._active["tp"] is None


def test_reversal_margin_filters_weak_reversal():
    """A reversal close that only grazes VAL is rejected by a big margin; the
    same feed with margin 0 (default) still fires."""
    k = _kernel()
    strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1, reversal_margin_pct=0.1)
    k.register_strategy(strat)
    _long_entry(k)                           # close 99.6 vs VAL ~97.2 → < 10% clear
    assert _fills(k) == []
    assert strat._active is None


def test_trail_back_uses_older_bar_for_ratchet():
    """trail_back loosens the BE-phase trail: the ratchet reads the bar N
    buckets back instead of the immediately-previous one, so a pullback that
    trips the tight trail survives the loose trail."""
    def run(trail_back):
        k = _kernel()
        strat = MorningVAHVAL(symbol=_NIFTY, lot_size=1, trail_back=trail_back)
        k.register_strategy(strat)
        _upday(k)
        _profile_window(k, D2)
        _reversal_long(k, D2)
        # 09:32 candle completes the entry bucket (BUY ~99.6) and seeds the
        # 09:32-33 bucket with a low ABOVE the entry so it never trips the
        # stop while reaching VAH → T1 → be phase (sl = entry = 99.6).
        _candle(k, ts=D2.replace(hour=9, minute=32), open_=99.6, high=103.0,
                low=100.0, close=102.0)
        _candle(k, ts=D2.replace(hour=9, minute=33), open_=102.0, high=102.9,
                low=100.2, close=102.4)
        # 09:34 completes the 09:32-33 bucket → T1 → be phase.
        _candle(k, ts=D2.replace(hour=9, minute=34), open_=102.4, high=102.6,
                low=100.1, close=102.3)
        assert strat._active["phase"] == "be"
        assert strat._active["sl"] == strat._active["entry"]
        # 09:34-35 bucket completes at 09:36 → BE ratchet reads the PREVIOUS
        # completed bucket (09:32-33, low 100.0 > entry) when trail_back=1,
        # but two buckets back (09:30-31, low 97.4 < entry) when trail_back=2.
        _candle(k, ts=D2.replace(hour=9, minute=35), open_=102.3, high=102.5,
                low=100.3, close=102.2)
        _candle(k, ts=D2.replace(hour=9, minute=36), open_=102.2, high=102.4,
                low=100.0, close=100.1)
        # Pullback bucket (09:36-37, completed at 09:38) dips below the tight
        # trail stop (100.0) but above the loose stop (99.6).
        _candle(k, ts=D2.replace(hour=9, minute=37), open_=100.1, high=100.3,
                low=99.9, close=100.0)
        _candle(k, ts=D2.replace(hour=9, minute=38), open_=100.0, high=100.1,
                low=99.8, close=99.9)
        return strat, k

    tight, kt = run(1)                        # classic: ratchet reads previous bar
    loose, _ = run(2)                         # looser: reads two buckets back
    # Tight trail ratcheted SL to the 09:32-33 low (100.0 > entry) → pullback
    # dips below 100.0 and trips the stop.
    assert tight._active is None
    stops = [s for s in _signals(kt) if s.metadata.get("exit_reason") == "stop"]
    assert len(stops) == 1
    # Loose trail kept SL at entry (99.6) → the pullback's low (99.8) stays
    # above it → position survives and is still managed.
    assert loose._active is not None
    assert loose._active["sl"] >= loose._active["entry"]
