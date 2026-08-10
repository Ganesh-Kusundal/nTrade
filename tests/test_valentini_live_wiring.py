"""ValentiniScalper — live/paper wiring tests (plan Task 7, Phase 7).

End-to-end through the real session stack — TradingSession.paper() →
StrategyRunner (scoped RiskEngine) → OrderEngine → BrokerExecution →
PaperBroker — with NO real orders. Covers:

  1. full feed->strategy->risk->paper-fill pipeline + portfolio update
  2. per-strategy risk caps reject oversized signals (and the strategy
     stays re-armable — the stale-_pending regression)
  3. risk halt -> LiveRunner kill switch: signals rejected, BrokerExecution
     circuit breaker forced open
  4. a synthetic feed source drives the kernel the way LiveRunner does
     (on_tick -> CandleEngine -> strategy.on_candle_closed)
"""

from datetime import date, datetime, timedelta

from ntrade.domain.instruments.derivatives import Future
from ntrade.engines.strategies import ValentiniScalper
from ntrade.events.market import CandleClosedEvent, QuoteEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.events.risk import SignalGeneratedEvent, SignalRejectedEvent
from ntrade.kernel.trading_session import TradingSession
from ntrade.runner.live_runner import LiveRunner

_TS = datetime(2026, 8, 3, 10, 0)   # within the 09:15-15:25 session gate
_EXPIRY = date(2026, 8, 27)
# _SYMBOL is derived from the Future fixture (NIFTY 27Aug26). Tests reference
# it after _paper_session() builds the future to stay in sync with the
# live harness symbol format.
_FUT_SYMBOL = None  # set by _paper_session() on first call


class _NullFeed:
    """Minimal MarketFeedSource stand-in for LiveRunner (no real socket)."""

    name = "null"
    ticks_published = 0

    def attach(self, kernel):
        self.kernel = kernel

    def start(self):
        return self

    def wait_ready(self, *, timeout=1.0, min_ticks=1):
        return True

    def stop(self):
        pass


def _paper_session(**strat_kw):
    """Paper session matching the live harness: a Future on NFO (MIS), not an
    Equity on NSE (CNC). The live harness registers ``session.future(...)``;
    paper tests must do the same so trade-type, SEBI conversion, and broker
    metadata all match (zero-parity)."""
    global _FUT_SYMBOL
    risk = strat_kw.pop("risk", {
        "max_quantity": 100_000, "max_daily_loss": 50_000.0,
    })
    sess = TradingSession.paper(initial_cash=1_000_000.0)
    nifty = sess.index("NIFTY")
    fut = sess.future(nifty, expiry=_EXPIRY)
    sess.register(fut)
    _FUT_SYMBOL = fut.symbol
    name = sess.register_strategy(ValentiniScalper(
        symbol=_FUT_SYMBOL, range_size=4.0, warmup=15, tp_multiplier=2.0,
        min_rr=1.5, **strat_kw), risk=risk)
    return sess, name


def _candle(sess, i, *, close, open_=None, high=None, low=None, volume=100):
    """Publish a candle's close as a QuoteEvent + CandleClosedEvent.

    Uses NFO exchange to match the Future instrument registered in
    _paper_session() (zero-parity with live harness)."""
    c = close
    o = open_ if open_ is not None else c - 0.5
    h = high if high is not None else max(c, o) + 0.5
    lo = low if low is not None else min(c, o) - 0.5
    ts = _TS + timedelta(minutes=i)
    symbol = _FUT_SYMBOL or "NIFTY 27Aug26"
    sess.kernel.bus.publish(QuoteEvent(
        symbol=symbol, exchange="NFO", ltp=c, bid=0.0, ask=0.0,
        open=o, high=h, low=lo, volume=volume, ts=ts))
    sess.kernel.bus.publish(CandleClosedEvent(
        symbol=symbol, exchange="NFO", timeframe="1m",
        open=o, high=h, low=lo, close=c, volume=volume, ts=ts))


def _uptrend(sess, n=30, start=100.0, step=0.5, volume=100):
    for i in range(n):
        _candle(sess, i, close=start + i * step, volume=volume)


def _downtrend(sess, n=30, start=130.0, step=0.5, volume=100):
    for i in range(n):
        _candle(sess, i, close=start - i * step, volume=volume)


def _absorption(sess, i, at, volume=1500):
    _candle(sess, i, close=at, open_=at - 0.02, high=at + 0.05,
            low=at - 0.05, volume=volume)


def _fills(sess):
    return [e for e in sess.kernel.bus.history if isinstance(e, OrderFilledEvent)]


def _signals(sess):
    return [e for e in sess.kernel.bus.history if isinstance(e, SignalGeneratedEvent)]


def _rejects(sess):
    return [e for e in sess.kernel.bus.history if isinstance(e, SignalRejectedEvent)]


def _make_synthetic_frame():
    """36 bars: 30 downtrend + absorption at VAL + consolidate + breakout, as
    1m OHLCV.

    This is the strategy's current signal contract: a BUY absorption must sit
    at the value edge (VAL), price consolidates near POC, then breaks above
    VWAP/POC. (An uptrend with a mid-value absorption never signals — the
    balance gate keeps it flat.)
    """
    import pandas as pd
    closes = [130 - i * 0.5 for i in range(30)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-08-03 09:15", periods=36, freq="1min"),
        "open": closes + [115.0, 116.0, 118.0, 120.0, 121.0, 121.0],
        "high": [c + 0.5 for c in closes] + [115.05, 116.5, 118.5, 120.5, 121.5, 121.5],
        "low": [c - 0.5 for c in closes] + [114.95, 115.5, 117.5, 119.5, 120.5, 120.5],
        "close": closes + [115.0, 116.0, 118.0, 120.0, 121.0, 121.0],
        "volume": [100] * 30 + [1500, 100, 100, 100, 100, 100],
    })


def _valentini_setup(sess):
    """Drive the 3 bars that complete the Triple-A setup and signal the entry:
    VAL absorption (bar 30), POC consolidation (31), breakout above VWAP (32)."""
    _downtrend(sess)
    _absorption(sess, 30, at=115.0)
    _candle(sess, 31, close=116.0)
    _candle(sess, 32, close=120.0)           # entry signal (above VWAP)


# ---------------------------------------------------------------- end to end

def test_paper_session_uses_future_instrument_not_equity():
    """Paper mode must use Future/NFO (matching live harness), not Equity/NSE.

    Equity defaults to CNC (delivery) trade type; Future defaults to MIS
    (intraday). The live harness registers a Future on NFO, so tests must
    too — otherwise paper fills use the wrong product type (parity bug).
    """
    sess, name = _paper_session()
    try:
        inst = sess.kernel.ctx.instrument(_FUT_SYMBOL)
        assert inst is not None, "future instrument not registered in kernel"
        assert isinstance(inst, Future), f"expected Future, got {type(inst).__name__}"
        assert inst.exchange == "NFO", f"expected NFO, got {inst.exchange}"
    finally:
        sess.stop()


def test_paper_session_end_to_end_fills_via_paper_broker():
    sess, name = _paper_session()
    try:
        _valentini_setup(sess)
        fills = _fills(sess)
        assert len(fills) == 1
        assert fills[0].side == "BUY"
        # PaperBroker fills carry PAPER- ids -> no real broker was touched.
        assert fills[0].order_id.startswith("PAPER-")
        assert fills[0].strategy == name
        # The kernel portfolio (the read-model strategies use, updated by
        # PortfolioEngine.on_filled) reflects the paper fill. Note: the
        # session-level portfolio() re-reads broker positions and PaperBroker
        # does not model them — the kernel read-model is the source of truth.
        pos = sess.kernel.ctx.portfolio.position(_FUT_SYMBOL)
        assert pos is not None and pos.quantity == fills[0].quantity
        # Scoped risk caps were applied on the runner.
        engine = sess.runner.risk(name)
        assert engine is not None and engine.max_quantity == 100_000
        assert engine.approved == 1 and engine.rejected == 0
    finally:
        sess.stop()

    # stop() cleared the session back-reference; broker disconnected cleanly
    assert sess.kernel.ctx.session is None


def test_risk_caps_reject_oversized_signal():
    # max_quantity 10 < the strategy's computed qty -> rejected.
    sess, name = _paper_session()
    try:
        # Reconfigure the scoped engine post-registration.
        engine = sess.runner.risk(name)
        engine.max_quantity = 10
        _valentini_setup(sess)
        assert _fills(sess) == []
        assert len(_rejects(sess)) == 1
        assert engine.rejected == 1
        # Rejected entry must not arm a phantom position; the strategy stays
        # re-armable for a later valid setup (stale-_pending regression).
        engine.max_quantity = None
        _absorption(sess, 33, at=117.0, volume=2000)
        _candle(sess, 34, close=118.5)
        _candle(sess, 35, close=120.0)
        assert any(f.side == "BUY" for f in _fills(sess))
    finally:
        sess.stop()


def test_risk_halt_trips_execution_and_rejects_signals():
    sess, name = _paper_session()
    runner = LiveRunner(sess.kernel, _NullFeed())
    try:
        runner.start()
        assert runner.started
        # Halt the scoped risk engine -> RiskHaltedEvent -> LiveRunner kill path.
        sess.runner.risk(name).halt("daily loss cap exceeded")
        assert runner.halted
        # A full setup now emits a signal that the halted risk rejects.
        _valentini_setup(sess)
        assert _fills(sess) == []
        assert any("halted" in getattr(e, "reason", "") for e in _rejects(sess))
        # BrokerExecution kill switch: circuit breaker forced OPEN.
        broker_exec = getattr(sess.kernel, "broker_execution", None)
        target = broker_exec() if callable(broker_exec) else broker_exec
        assert target is not None
        assert target._breaker.state.value == "OPEN"
    finally:
        runner.stop()
        sess.stop()


def test_synthetic_feed_drives_strategy_via_ticks():
    """The LiveRunner path: a feed's ticks -> CandleEngine -> strategy candles.

    Uses a Future on NFO to match the live harness instrument type (parity).
    """
    from ntrade.kernel.session import TradingKernel
    from ntrade.runner.feeds import build_source

    sess, strat_name = _paper_session()
    try:
        k = sess.kernel
        frame = _make_synthetic_frame()
        source = build_source(k, feed="synth", symbol=_FUT_SYMBOL,
                              exchange="NFO", frame=frame)
        runner = LiveRunner(k, source, poll_interval=60.0, sync_interval=60.0)
        try:
            runner.run(duration=5.0)
            fills = [e for e in k.bus.history if isinstance(e, OrderFilledEvent)]
            assert fills, "synthetic feed should produce a valentini entry"
            assert source.ticks_published > 0
        finally:
            runner.stop()
    finally:
        sess.stop()


def test_simulated_depth_drives_depth_imbalance_filter():
    """The simulated order book makes the depth filter exercisable offline.

    With ``depth_imbalance_min`` set and ``depth_levels>0`` the synthetic feed's
    per-bar DepthEvents populate the read-model book, so the strategy's
    confidence filter actually gates the entry — a sell-biased book blocks the
    BUY, a buy-biased book lets it through. Without the depth layer this same
    setup would skip the filter (zero-parity default).

    Uses a Future on NFO (parity with live harness).
    """
    from ntrade.kernel.session import TradingKernel
    from ntrade.runner.feeds import build_source

    sess, _strat = _paper_session()
    try:
        k = sess.kernel
        frame = _make_synthetic_frame()

        def run_with(depth_imbalance: float):
            # Fresh kernel per run: positions/fills must not carry over.
            s2 = TradingSession.paper(initial_cash=1_000_000.0)
            nifty = s2.index("NIFTY")
            fut = s2.future(nifty, expiry=_EXPIRY)
            s2.register(fut)
            symbol = fut.symbol
            s2.register_strategy(ValentiniScalper(
                symbol=symbol, range_size=4.0, warmup=15, tp_multiplier=2.0,
                min_rr=1.5, depth_imbalance_min=0.3), risk={"max_quantity": 100_000})
            source = build_source(
                s2.kernel, feed="synth", symbol=symbol, exchange="NFO",
                frame=frame, depth_levels=5, depth_imbalance=depth_imbalance,
                depth_seed=9)
            runner = LiveRunner(s2.kernel, source, poll_interval=60.0, sync_interval=60.0)
            try:
                runner.run(duration=5.0)
                return [e for e in s2.kernel.bus.history
                        if isinstance(e, OrderFilledEvent)]
            finally:
                runner.stop()
                s2.stop()

        # Sell-biased book (imbalance < +0.3) -> BUY setup blocked by the filter.
        assert run_with(depth_imbalance=-0.6) == []
        # Buy-biased book (imbalance >= +0.3) -> same setup now fires.
        assert any(f.side == "BUY" for f in run_with(depth_imbalance=0.6))
    finally:
        sess.stop()
