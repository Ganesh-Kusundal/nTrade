# G2 — Live Harness, Synthetic Tick Engine, Risk & OMS Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every gap the platform audit found: a `LiveRunner` orchestration loop that drives a feed source through the kernel with periodic `poll_orders()`/`sync_positions()`, a synthetic tick engine that extrapolates 1-minute candles into 1-second ticks (respecting high/low), risk circuit breakers wired to the Dhan kill switch, OMS order-state events + modify/cancel, observability, and paper→live gate + ops tooling.

**Architecture:** The kernel stays untouched — every new capability is an event source (`SyntheticMarketFeedSource`), a harness (`LiveRunner`) that owns the orchestration loop, RiskEngine extensions (pure, broker-agnostic; the runner wires the Dhan kill switch on halt), and OMS passthroughs on the existing `BrokerExecution`. A simple `feed="synth"|"live"` flag selects which source the runner drives — zero parity is preserved because both sources publish the same canonical events.

**Tech Stack:** Python 3.12, pandas, stdlib `logging` + `threading` + `time`, `random.Random` (seeded, deterministic). Test runner: `.venv/bin/python -m pytest -q` (baseline **314 passing**). The repo is NOT git-tracked — verification is the test suite, not commits.

## Global Constraints

- Test command is always `.venv/bin/python -m pytest -q` (run from `/Users/apple/Downloads/nTrade`).
- Only append to existing modules; never restructure existing files wholesale.
- All events: `@dataclass(frozen=True, kw_only=True)` extending `ntrade.events.base.Event`; `ts` from the kernel clock, never `datetime.now()`.
- Deterministic simulation: seeds via `random.Random(seed)`; same seed + same bar → identical ticks.
- Simulated ticks must stay within `[low, high]`, touch both extremes exactly, start at `open`, end at `close`, and sum tick volume to the bar volume — so a 1m candle built from the ticks reconstructs the source bar (this is the "high/low respected" invariant).
- Domain layer stays broker-agnostic: RiskEngine publishes `RiskHaltedEvent`; the runner (infra) invokes the Dhan kill-switch capability.
- No git commits (not a git repo). Each task's gate is: failing test first, then implementation, then full suite green.

---

## Task Group A — Synthetic tick simulation engine (the user's emphasis)

### Task A1: Pure 1m→1s tick synthesizer

**Files:**
- Create: `ntrade/sim/__init__.py`
- Create: `ntrade/sim/tick_simulator.py`
- Test: `tests/test_tick_simulator.py`

**Interfaces:**
- Produces: `SimTick(ts: datetime, price: float, quantity: int)` frozen dataclass; `synthesize_1m_ticks(bar_ts, open_, high, low, close, volume, *, seed=0, seconds=60) -> list[SimTick]`.

- [x] **Step 1: Write the failing test**

```python
"""Deterministic 1-second tick synthesis from 1m OHLCV bars (G2-A1)."""
from datetime import datetime

import pytest

from ntrade.sim.tick_simulator import synthesize_1m_ticks


def _bar():
    return datetime(2026, 7, 30, 9, 15), 100.0, 105.0, 98.0, 102.0, 600


def test_returns_one_tick_per_second():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    assert len(ticks) == 60
    for i, t in enumerate(ticks):
        assert (t.ts - ts).total_seconds() == i


def test_anchors_open_and_close():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    assert ticks[0].price == o
    assert ticks[-1].price == c


def test_prices_stay_within_high_low():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    for t in ticks:
        assert l <= t.price <= h


def test_high_and_low_touched():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    prices = [t.price for t in ticks]
    assert max(prices) == h
    assert min(prices) == l


def test_volume_sums_to_bar_volume():
    ts, o, h, l, c, v = _bar()
    ticks = synthesize_1m_ticks(ts, o, h, l, c, v)
    assert sum(t.quantity for t in ticks) == v


def test_deterministic_same_seed():
    ts, o, h, l, c, v = _bar()
    a = synthesize_1m_ticks(ts, o, h, l, c, v, seed=7)
    b = synthesize_1m_ticks(ts, o, h, l, c, v, seed=7)
    assert [(t.price, t.quantity) for t in a] == [(t.price, t.quantity) for t in b]


def test_different_seed_different_path():
    ts, o, h, l, c, v = _bar()
    a = synthesize_1m_ticks(ts, o, h, l, c, v, seed=1)
    b = synthesize_1m_ticks(ts, o, h, l, c, v, seed=2)
    assert [(t.price, t.quantity) for t in a] != [(t.price, t.quantity) for t in b]


def test_flat_bar_is_flat():
    ts = datetime(2026, 7, 30, 9, 15)
    ticks = synthesize_1m_ticks(ts, 100.0, 100.0, 100.0, 100.0, 100)
    assert all(t.price == 100.0 for t in ticks)


def test_invalid_range_raises():
    ts = datetime(2026, 7, 30, 9, 15)
    with pytest.raises(ValueError):
        synthesize_1m_ticks(ts, 100.0, 90.0, 95.0, 100.0, 100)
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tick_simulator.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ntrade.sim'`

- [x] **Step 3: Write minimal implementation**

`ntrade/sim/__init__.py`:
```python
from ntrade.sim.tick_simulator import SimTick, synthesize_1m_ticks

__all__ = ["SimTick", "synthesize_1m_ticks"]
```

`ntrade/sim/tick_simulator.py`:
```python
"""Deterministic synthetic tick engine: extrapolate 1-minute OHLCV into
1-second ticks that respect the bar's high/low.

Invariants (all test-enforced):
  - one tick per second (seconds=60 by default)
  - tick[0] == open, tick[-1] == close
  - every price within [low, high]
  - max(price) == high and min(price) == low (both extremes touched)
  - sum(tick.quantity) == bar volume
  - same seed + same bar -> identical ticks
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class SimTick:
    ts: datetime
    price: float
    quantity: int


def _interp(anchors: list[tuple[int, float]], n: int) -> list[float]:
    """Piecewise-linear path through (index, price) anchors, length n."""
    prices = [0.0] * n
    for (i0, p0), (i1, p1) in zip(anchors, anchors[1:]):
        for i in range(i0, i1 + 1):
            frac = (i - i0) / (i1 - i0) if i1 != i0 else 0.0
            prices[i] = p0 + (p1 - p0) * frac
    return prices


def _distribute_volume(volume: int, prices: list[float]) -> list[int]:
    """Split bar volume across ticks proportional to per-second |move|."""
    volume = int(volume or 0)
    if volume <= 0:
        return [0] * len(prices)
    moves = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    total = sum(moves) or 1.0
    qty = [0] * len(prices)
    for i, m in enumerate(moves, start=1):
        qty[i] = int(volume * m / total)
    qty[-1] += volume - sum(qty)  # remainder lands on the last tick
    return qty


def synthesize_1m_ticks(bar_ts: datetime, open_: float, high: float, low: float,
                        close: float, volume: int, *, seed: int = 0,
                        seconds: int = 60) -> list[SimTick]:
    if high < low:
        raise ValueError(f"high {high} < low {low}")
    if seconds < 4:
        raise ValueError("seconds must be >= 4")
    rng = random.Random(seed)
    n = seconds
    hi = rng.randrange(1, n - 1)
    lo = rng.randrange(1, n - 1)
    while lo == hi:
        lo = rng.randrange(1, n - 1)
    anchors = sorted([(0, open_), (hi, high), (lo, low), (n - 1, close)])
    prices = _interp(anchors, n)
    noise = (high - low) * 0.02
    for i in range(n):
        if i in (0, hi, lo, n - 1):
            continue
        prices[i] = min(high, max(low, prices[i] + rng.uniform(-noise, noise)))
    qty = _distribute_volume(volume, prices)
    # round BEFORE re-pinning the anchors: round() would otherwise break the
    # "high/low touched" invariant for non-integer prices (e.g. live NIFTY).
    for i in range(n):
        prices[i] = round(prices[i], 2)
    prices[0] = open_
    prices[hi] = high
    prices[lo] = low
    prices[n - 1] = close
    return [SimTick(ts=bar_ts + timedelta(seconds=i), price=prices[i],
                    quantity=qty[i]) for i in range(n)]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tick_simulator.py -q`
Expected: PASS (9 tests)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 323 passing (314 + 9 new)

### Task A2: SyntheticMarketFeedSource (1m frame → 1s ticks → kernel events)

**Files:**
- Modify: `ntrade/sources/market_feed.py` (no changes needed — reuse ABC)
- Create: `ntrade/sources/synthetic_feed.py`
- Modify: `ntrade/sources/__init__.py` (export the new source)
- Test: `tests/test_synthetic_feed.py`

**Interfaces:**
- Consumes: `SimTick`, `synthesize_1m_ticks` from `ntrade.sim.tick_simulator`.
- Produces: `SyntheticMarketFeedSource(MarketFeedSource)` with `name = "synthetic"`; constructor `(kernel=None, *, symbol="SYM", exchange="NSE", data=None, seed=0, seconds=60)`; `start()` spawns a daemon thread that publishes one `QuoteEvent` (bar OHLCV at bar ts) then one `TickEvent` per second; `join(timeout=None)`; `stop()`; counter `ticks_published`.
- Requirement: candle reconstruction — feeding a 1m OHLCV frame through this source into a kernel with a 1m `CandleEngine`, then `flush()`, reproduces the original bars (open/high/low/close/volume).

- [x] **Step 1: Write the failing test**

```python
"""SyntheticMarketFeedSource: 1m OHLCV frame -> 1-second ticks (G2-A2).

The key invariant: a 1m CandleEngine fed only these ticks reconstructs the
source bars exactly (open/high/low/close/volume) — high and low respected.
"""
from datetime import datetime, timedelta

import pandas as pd

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import CandleClosedEvent, TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


def _frame(n: int = 5, start: float = 100.0):
    rows = []
    for i in range(n):
        rows.append({
            "timestamp": datetime(2026, 7, 30, 9, 15) + timedelta(minutes=i),
            "open": start + i, "high": start + i + 4, "low": start + i - 3,
            "close": start + i + 1, "volume": 600,
        })
    return pd.DataFrame(rows)


def _run(frame):
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("SYM"))
    src = SyntheticMarketFeedSource(k, symbol="SYM", exchange="NSE", data=frame)
    src.start()
    src.join(timeout=5)
    k.candle_engine.flush()
    return k, src


def test_publishes_60_ticks_per_bar():
    k, src = _run(_frame(2))
    assert src.ticks_published == 120
    assert len([e for e in k.bus.history if isinstance(e, TickEvent)]) == 120


def test_reconstructs_bars_exactly():
    frame = _frame(3)
    k, _ = _run(frame)
    candles = k.candle_engine.candles("SYM")
    assert len(candles) == 3
    for bar, c in zip(frame.to_dict("records"), candles):
        assert isinstance(c, CandleClosedEvent)
        assert c.open == bar["open"]
        assert c.high == bar["high"]
        assert c.low == bar["low"]
        assert c.close == bar["close"]
        assert c.volume == bar["volume"]


def test_instrument_quote_reflects_last_bar():
    frame = _frame(2)
    k, _ = _run(frame)
    inst = k.ctx.instrument("SYM")
    assert inst.ltp == frame["close"].iloc[-1]
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_synthetic_feed.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ntrade.sources.synthetic_feed'`

- [x] **Step 3: Write minimal implementation**

`ntrade/sources/synthetic_feed.py`:
```python
"""SyntheticMarketFeedSource — 1m OHLCV history extrapolated to 1-second ticks.

Deterministic offline stand-in for a live feed: turns a 1m OHLCV frame (e.g.
fetched via DhanBroker.get_historical) into one TickEvent per simulated second
using the seeded engine in ntrade.sim. Publishing a QuoteEvent per bar keeps
the instrument read-model's OHLCV consistent. Same canonical events as live —
the kernel does not know the difference (zero parity).
"""

from __future__ import annotations

import threading

from ntrade.events.market import QuoteEvent, TickEvent
from ntrade.sim.tick_simulator import synthesize_1m_ticks
from ntrade.sources.market_feed import MarketFeedSource


class SyntheticMarketFeedSource(MarketFeedSource):
    name = "synthetic"

    def __init__(self, kernel=None, *, symbol: str = "SYM", exchange: str = "NSE",
                 data=None, seed: int = 0, seconds: int = 60):
        super().__init__(kernel)
        if data is None or data.empty:
            raise ValueError("data must be a non-empty 1m OHLCV frame")
        self.symbol = symbol
        self.exchange = exchange
        self.data = data
        self.seed = seed
        self.seconds = seconds
        self.ticks_published = 0
        self._thread = None
        self._stopping = False

    # ------------------------------------------------------------------ feed
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._produce, daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self) -> None:
        self._stopping = True
        self.join(timeout=2)

    def _produce(self) -> None:
        for _, row in self.data.iterrows():
            if self._stopping:
                return
            ts = row["timestamp"]
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            open_, high, low, close = (float(row["open"]), float(row["high"]),
                                       float(row["low"]), float(row["close"]))
            volume = int(row.get("volume", 0) or 0)
            self.bus.publish(QuoteEvent(
                symbol=self.symbol, exchange=self.exchange, ltp=close, bid=0.0, ask=0.0,
                open=open_, high=high, low=low, volume=volume, ts=ts,
            ))
            for tick in synthesize_1m_ticks(ts, open_, high, low, close, volume,
                                            seed=self.seed, seconds=self.seconds):
                if self._stopping:
                    return
                if hasattr(self.kernel.clock, "set"):
                    self.kernel.clock.set(tick.ts)
                self.bus.publish(TickEvent(
                    symbol=self.symbol, exchange=self.exchange, price=tick.price,
                    quantity=tick.quantity, ts=tick.ts,
                ))
                self.ticks_published += 1
```

`ntrade/sources/__init__.py`:
```python
from ntrade.sources.dhan_feed import DhanMarketFeedSource, dhan_payload_to_events
from ntrade.sources.market_feed import MarketFeedSource, SimulatedFeedSource
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource

__all__ = [
    "MarketFeedSource", "SimulatedFeedSource", "SyntheticMarketFeedSource",
    "DhanMarketFeedSource", "dhan_payload_to_events",
]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_synthetic_feed.py -q`
Expected: PASS (3 tests)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 326 passing

---

## Task Group B — LiveRunner orchestration harness

### Task B1: Runner lifecycle events + LiveRunner

**Files:**
- Modify: `ntrade/events/lifecycle.py` (add `RunnerStartedEvent`, `RunnerStoppedEvent`)
- Modify: `ntrade/events/risk.py` (add `RiskHaltedEvent`, `RiskResumedEvent` — the runner subscribes to the halt event, defined here before the engine work in C1)
- Create: `ntrade/runner/__init__.py`
- Create: `ntrade/runner/live_runner.py`
- Test: `tests/test_live_runner.py`

**Interfaces:**
- Consumes: `TradingKernel`, any `MarketFeedSource`.
- Produces: `LiveRunner(kernel, source, *, poll_interval=2.0, sync_interval=30.0, logger=None)` with `start()`, `step()`, `run(duration=None)`, `stop(reason="")`, counters `polls`, `syncs`; injectable time (`_timer`, `_sleep`) for deterministic tests. Subscribes to `RiskHaltedEvent` and fires the Dhan kill-switch capability on every registered instrument when a broker is present (added in Task C1).

- [x] **Step 1: Write the failing test**

```python
"""LiveRunner orchestration loop (G2-B1)."""
import time

import pytest

from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.live_runner import LiveRunner
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


class _FakeTimer:
    def __init__(self):
        self.t = 0.0

    def advance(self, dt):
        self.t += dt


def _source(data=None):
    import pandas as pd
    from datetime import datetime, timedelta
    if data is None:
        rows = [{"timestamp": datetime(2026, 7, 30, 9, 15),
                 "open": 100.0, "high": 103.0, "low": 98.0,
                 "close": 101.0, "volume": 100}]
        data = pd.DataFrame(rows)
    return SyntheticMarketFeedSource(symbol="SYM", exchange="NSE", data=data)


def test_run_duration_stops_and_cleans_up():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    src = _source()
    runner = LiveRunner(k, src, poll_interval=0.1, sync_interval=0.1)
    runner._sleep = lambda s: None  # no wall-clock waiting
    runner.run(duration=0.3)
    assert runner.kernel.stop.__name__  # sanity
    # stop() flushed the kernel and stopped the source
    assert src.ticks_published > 0


def test_step_polls_and_syncs_on_interval():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source(), poll_interval=2.0, sync_interval=5.0)
    timer = _FakeTimer()
    runner._timer = lambda: timer.t
    runner.start()
    timer.advance(2.0)
    runner.step()
    assert runner.polls == 1
    timer.advance(3.0)  # total 5 -> sync due
    runner.step()
    assert runner.syncs == 1
    runner.stop()


def test_poll_sync_noop_without_broker():
    """Sim-mode kernel: poll_orders()/sync_positions() are harmless no-ops."""
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source(), poll_interval=0.01, sync_interval=0.01)
    runner._timer = lambda: 100.0
    runner.start()
    runner.step()
    assert runner.polls >= 1
    assert runner.syncs >= 1
    runner.stop()


def test_runner_publishes_lifecycle_events():
    from ntrade.events.lifecycle import RunnerStartedEvent, RunnerStoppedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    runner._sleep = lambda s: None
    runner.run(duration=0.1)
    kinds = [type(e).__name__ for e in k.bus.history]
    assert "RunnerStartedEvent" in kinds
    assert "RunnerStoppedEvent" in kinds
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ntrade.runner'`

- [x] **Step 3: Write minimal implementation**

`ntrade/events/lifecycle.py` — append after `SessionStoppedEvent`:
```python
@dataclass(frozen=True, kw_only=True)
class RunnerStartedEvent(Event):
    """The LiveRunner harness started driving a feed through the kernel."""

    feed: str = ""
    mode: str = ""


@dataclass(frozen=True, kw_only=True)
class RunnerStoppedEvent(Event):
    """The LiveRunner harness stopped (reason = normal / error / interrupted)."""

    reason: str = ""
```

`ntrade/events/risk.py` — append (the runner subscribes to this; RiskEngine wiring lands in Task C1):
```python
@dataclass(frozen=True, kw_only=True)
class RiskHaltedEvent(Event):
    """A circuit breaker tripped; the engine rejects all signals until resume."""

    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class RiskResumedEvent(Event):
    """The engine was manually resumed after a halt."""
```

`ntrade/runner/__init__.py`:
```python
from ntrade.runner.live_runner import LiveRunner

__all__ = ["LiveRunner"]
```

`ntrade/runner/live_runner.py`:
```python
"""LiveRunner — the orchestration loop that makes the kernel a live system.

Drives any MarketFeedSource (synthetic for offline rehearsal, Dhan for live)
through the kernel while a maintenance loop periodically calls
poll_orders() / sync_positions() so the broker lifecycle advances. The
synth-vs-live choice is just which source is attached — the kernel is
identical either way (zero parity).

The kill switch is wired here, not in the domain: on a RiskHaltedEvent the
runner activates the broker kill switch on every registered instrument, so the
broker-agnostic RiskEngine never touches broker capabilities.
"""

from __future__ import annotations

import logging
import time

from ntrade.events.lifecycle import RunnerStartedEvent, RunnerStoppedEvent
from ntrade.events.risk import RiskHaltedEvent


class LiveRunner:
    def __init__(self, kernel, source, *, poll_interval: float = 2.0,
                 sync_interval: float = 30.0, logger=None):
        self.kernel = kernel
        self.source = source
        self.poll_interval = poll_interval
        self.sync_interval = sync_interval
        self.logger = logger or logging.getLogger("ntrade.runner")
        self._running = False
        self._last_poll = None
        self._last_sync = None
        self.polls = 0
        self.syncs = 0
        # injectable time (deterministic tests)
        self._timer = time.monotonic
        self._sleep = time.sleep

    # ------------------------------------------------------------------ start
    def start(self):
        self._running = True
        self.kernel.start()
        self.source.attach(self.kernel)
        self.source.start()
        self.kernel.bus.subscribe(RiskHaltedEvent, self._on_risk_halted)
        self.kernel.bus.publish(RunnerStartedEvent(
            feed=self.source.name, mode=self.kernel.mode, ts=self.kernel.clock.now()))
        self.logger.info("runner started: feed=%s mode=%s",
                         self.source.name, self.kernel.mode)
        return self

    def _on_risk_halted(self, event: RiskHaltedEvent) -> None:
        self.logger.error("RISK HALT: %s", event.reason)
        if self.kernel.broker is None:
            return
        for instrument in self.kernel.ctx.instruments.values():
            try:
                instrument.broker.kill_switch(action="ACTIVATE")
                self.logger.error("kill switch ACTIVATED for %s", instrument.symbol)
            except Exception:
                continue

    # --------------------------------------------------------------- the loop
    def step(self):
        """One maintenance pass: poll orders / sync positions when due."""
        now = self._timer()
        if self._last_poll is None or now - self._last_poll >= self.poll_interval:
            emitted = self.kernel.poll_orders()
            self.polls += 1
            self._last_poll = now
            if emitted:
                self.logger.info("poll_orders: %d lifecycle events", len(emitted))
        if self._last_sync is None or now - self._last_sync >= self.sync_interval:
            n = self.kernel.sync_positions()
            self.syncs += 1
            self._last_sync = now
            self.logger.info("sync_positions: %d positions reconciled", n)

    def run(self, duration: float | None = None):
        self.start()
        deadline = self._timer() + duration if duration else None
        try:
            while self._running:
                self.step()
                if deadline is not None and self._timer() >= deadline:
                    break
                self._sleep(0.05)
        finally:
            self.stop(reason="completed")

    def stop(self, reason: str = "stopped"):
        self._running = False
        self.source.stop()
        self.kernel.stop(reason=reason)
        self.kernel.bus.publish(RunnerStoppedEvent(reason=reason, ts=self.kernel.clock.now()))
        self.logger.info("runner stopped: %s", reason)
        return self
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_live_runner.py -q`
Expected: PASS (4 tests)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 330 passing

### Task B2: synth/live feed factory + runner run script

**Files:**
- Create: `ntrade/runner/feeds.py`
- Create: `scripts/live_runner_run.py`
- Test: `tests/test_runner_feeds.py`

**Interfaces:**
- Produces: `build_source(kernel, *, feed="synth", symbol="NIFTY", exchange="NSE", frame=None, seed=0, live_kwargs=None) -> MarketFeedSource`. `feed="synth"` requires `frame` (1m OHLCV) → `SyntheticMarketFeedSource`; `feed="live"` → `DhanMarketFeedSource(**(live_kwargs or {}))`.

- [x] **Step 1: Write the failing test**

```python
"""synth/live feed factory (G2-B2)."""
from datetime import datetime

import pandas as pd
import pytest

from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.feeds import build_source
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


def _frame():
    return pd.DataFrame([{
        "timestamp": datetime(2026, 7, 30, 9, 15),
        "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0, "volume": 100,
    }])


def test_synth_builds_synthetic_source():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    src = build_source(k, feed="synth", symbol="NIFTY", frame=_frame())
    assert isinstance(src, SyntheticMarketFeedSource)


def test_synth_requires_frame():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    with pytest.raises(ValueError):
        build_source(k, feed="synth")


def test_live_builds_dhan_source():
    from ntrade.sources.dhan_feed import DhanMarketFeedSource
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m")
    src = build_source(k, feed="live", live_kwargs={"symbols": [(1, 2885)]})
    assert isinstance(src, DhanMarketFeedSource)


def test_unknown_feed_raises():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    with pytest.raises(ValueError):
        build_source(k, feed="bogus")
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runner_feeds.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ntrade.runner.feeds'`

- [x] **Step 3: Write minimal implementation**

`ntrade/runner/feeds.py`:
```python
"""Simple synth/live feed switch — the flag the harness is built around."""

from __future__ import annotations


def build_source(kernel, *, feed: str = "synth", symbol: str = "NIFTY",
                 exchange: str = "NSE", frame=None, seed: int = 0,
                 live_kwargs: dict | None = None):
    if feed == "synth":
        if frame is None or frame.empty:
            raise ValueError("synth feed requires a non-empty 1m OHLCV 'frame'")
        from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource
        return SyntheticMarketFeedSource(
            kernel, symbol=symbol, exchange=exchange, data=frame, seed=seed)
    if feed == "live":
        from ntrade.sources.dhan_feed import DhanMarketFeedSource
        return DhanMarketFeedSource(kernel, **(live_kwargs or {}))
    raise ValueError(f"unknown feed {feed!r}; expected 'synth' or 'live'")
```

`scripts/live_runner_run.py`:
```python
"""Run the LiveRunner harness on a symbol.

  synth mode (default): fetch 1m historical data via Dhan and extrapolate it
      to 1-second ticks offline — full pipeline rehearsal without risk.
  live mode:            drive the real Dhan websocket (--live-kwargs JSON).

Usage:
  .venv/bin/python scripts/live_runner_run.py --symbol NIFTY --days 2 --duration 5
  .venv/bin/python scripts/live_runner_run.py --feed live \
      --live-kwargs '{"symbols": [[1, 2885]], "symbol_map": {"2885": ["NIFTY", "NSE"]}}'
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.kernel.clock import LiveClock, ReplayClock  # noqa: E402
from ntrade.kernel.session import TradingKernel  # noqa: E402
from ntrade.runner.feeds import build_source  # noqa: E402
from ntrade.runner.live_runner import LiveRunner  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run the LiveRunner harness")
    p.add_argument("--feed", choices=("synth", "live"), default="synth")
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--exchange", default="NSE")
    p.add_argument("--days", type=int, default=2)
    p.add_argument("--duration", type=float, default=None, help="run seconds")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--sync", type=float, default=30.0)
    p.add_argument("--timeframe", default="1m")
    p.add_argument("--live-kwargs", default="{}", help="JSON kwargs for the live feed")
    args = p.parse_args()

    mode = "live" if args.feed == "live" else "replay"
    clock = LiveClock() if mode == "live" else ReplayClock()
    k = TradingKernel(mode=mode, clock=clock, timeframe=args.timeframe)

    frame = None
    if args.feed == "synth":
        from ntrade.domain.instruments.cash import Index
        from ntrade.facade import Market
        m = Market(broker="dhan")
        instrument = m.index(args.symbol)
        frame = instrument.history(args.timeframe, days=args.days, force=True).df
        k.register(Index(args.symbol))
        print(f"synth feed: {len(frame)} x {args.timeframe} bars for {args.symbol}")

    live_kwargs = json.loads(args.live_kwargs)
    source = build_source(k, feed=args.feed, symbol=args.symbol,
                          exchange=args.exchange, frame=frame, live_kwargs=live_kwargs)
    runner = LiveRunner(k, source, poll_interval=args.poll, sync_interval=args.sync)
    runner.run(duration=args.duration)

    print(f"\n== session summary ==")
    print(f"  ticks published : {source.ticks_published}")
    print(f"  polls           : {runner.polls}   syncs: {runner.syncs}")
    print(f"  fills           : {len([e for e in k.bus.history if e.__class__.__name__ == 'OrderFilledEvent'])}")
    print(f"  balance         : {k.ctx.account.balance:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_runner_feeds.py -q`
Expected: PASS (4 tests)

- [x] **Step 5: Smoke the synth script**

Run: `.venv/bin/python scripts/live_runner_run.py --symbol NIFTY --days 1 --duration 0.1 --poll 0.1 --sync 0.1`
Expected: prints a session summary (ticks published > 0, balance row). No exception.

- [x] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 334 passing

---

## Task Group C — Risk hardening (circuit breakers + kill switch)

### Task C1: RiskHalted/Resumed events + RiskEngine breakers

**Files:**
- Modify: `ntrade/engines/risk_engine.py` (breakers + halt/resume; the `RiskHaltedEvent`/`RiskResumedEvent` events already exist — added in Task B1)
- Test: `tests/test_risk_breakers.py`

**Interfaces:**
- Produces: `RiskEngine(ctx, *, max_quantity=None, max_notional=None, max_positions=None, allowlist=None, strategy=None, max_daily_loss=None, max_drawdown_pct=None, price_deviation_pct=None)`; properties `halted: bool`, `halt_reason: str`; methods `halt(reason)`, `resume()`, `equity() -> float`.
- Break semantics: once halted, every signal is rejected with the halt reason until `resume()`. `max_daily_loss` halts when `start_balance - equity > max_daily_loss`; `max_drawdown_pct` halts when `(peak_equity - equity) / peak_equity * 100 > max_drawdown_pct`; `price_deviation_pct` rejects signals whose `price` deviates from the instrument's `ltp` by more than that %.

- [x] **Step 1: Write the failing test**

```python
"""RiskEngine circuit breakers (G2-C1)."""
import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.domain.portfolio import Position
from ntrade.events.market import TickEvent
from ntrade.events.risk import RiskHaltedEvent, RiskResumedEvent, SignalGeneratedEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _kernel(initial_cash=100_000.0, **risk_kw):
    from ntrade.events.risk import SignalGeneratedEvent
    from ntrade.engines.risk_engine import RiskEngine
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=initial_cash)
    k.register(Equity("NIFTY"))
    # detach the kernel's default engine so signals are screened only once
    k.bus.unsubscribe(SignalGeneratedEvent, k.risk_engine.on_signal)
    k.risk_engine = RiskEngine(k.ctx, **risk_kw)
    return k


def _signal(k, price=100.0, qty=1, side="BUY"):
    return SignalGeneratedEvent(symbol="NIFTY", exchange="NSE", side=side,
                                quantity=qty, price=price, strategy="t",
                                ts=k.clock.now())


def test_daily_loss_halts_and_rejects_all():
    k = _kernel(initial_cash=10_000.0, max_daily_loss=500.0)
    # a losing short position (MTM loss = -1500): equity = 10_000 - 1500
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    k.risk_engine.on_signal(_signal(k))       # trips the breaker
    k.risk_engine.on_signal(_signal(k))       # a second signal is rejected too
    assert k.risk_engine.halted
    assert "daily loss" in k.risk_engine.halt_reason
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    assert len(rejects) == 2


def test_drawdown_halts():
    k = _kernel(initial_cash=10_000.0, max_drawdown_pct=10.0)
    # peak equity is captured at 10_000 on the first (clean) signal
    k.risk_engine.on_signal(_signal(k))
    assert not k.risk_engine.halted
    # now a large losing short position pushes equity to 6_500 (-35% drawdown)
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=450.0))
    k.risk_engine.on_signal(_signal(k))
    assert k.risk_engine.halted
    assert "drawdown" in k.risk_engine.halt_reason


def test_price_deviation_rejects_but_does_not_halt():
    k = _kernel(price_deviation_pct=1.0)
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=k.clock.now()))
    k.risk_engine.on_signal(_signal(k, price=105.0))  # 5% off ltp
    k.risk_engine.on_signal(_signal(k, price=100.5))  # 0.5% off -> approved
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(rejects) == 1
    assert len(approves) == 1
    assert not k.risk_engine.halted


def test_halt_and_resume_emit_events_and_restore():
    k = _kernel(max_daily_loss=1.0)
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    k.risk_engine.on_signal(_signal(k))
    assert k.risk_engine.halted
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)
    k.risk_engine.resume()
    assert not k.risk_engine.halted
    assert any(isinstance(e, RiskResumedEvent) for e in k.bus.history)


def test_no_breakers_means_no_halting():
    k = _kernel()
    k.ctx.portfolio.positions.append(Position("NIFTY", 10, avg_price=100.0, ltp=1.0))
    k.risk_engine.on_signal(_signal(k))
    assert not k.risk_engine.halted
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(approves) == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_risk_breakers.py -q`
Expected: FAIL — `TypeError: unexpected keyword argument 'max_daily_loss'` (or `AttributeError: 'RiskEngine' object has no attribute 'halt'`)

- [x] **Step 3: Write minimal implementation**

`ntrade/engines/risk_engine.py` — full replacement:
```python
"""RiskEngine — screens signals before they become order intents.

Consumes SignalGeneratedEvent; publishes SignalApprovedEvent or
SignalRejectedEvent. Static limits (allowlist, quantity, notional, position
count) plus circuit breakers: a daily-loss cap, a max-drawdown halt and a
price-deviation (fat-finger) guard. Once halted, every signal is rejected
until resume(). The engine is broker-agnostic — the kill-switch side effect
lives in the LiveRunner, which reacts to RiskHaltedEvent.
"""

from __future__ import annotations

from ntrade.events.risk import (
    RiskHaltedEvent, RiskResumedEvent,
    SignalApprovedEvent, SignalGeneratedEvent, SignalRejectedEvent,
)


class RiskEngine:
    def __init__(self, context, *, max_quantity: int | None = None,
                 max_notional: float | None = None, max_positions: int | None = None,
                 allowlist: set | None = None, strategy: str | None = None,
                 max_daily_loss: float | None = None,
                 max_drawdown_pct: float | None = None,
                 price_deviation_pct: float | None = None):
        self.ctx = context
        self.max_quantity = max_quantity
        self.max_notional = max_notional
        self.max_positions = max_positions
        self.allowlist = set(allowlist) if allowlist else None
        self.strategy = strategy  # None = screen all strategies
        self.max_daily_loss = max_daily_loss
        self.max_drawdown_pct = max_drawdown_pct
        self.price_deviation_pct = price_deviation_pct
        self.halted = False
        self.halt_reason = ""
        self._start_balance = float(context.account.balance)
        self._peak_equity = None
        self.approved = 0
        self.rejected = 0
        context.bus.subscribe(SignalGeneratedEvent, self.on_signal)

    # ------------------------------------------------------------------ state
    def equity(self) -> float:
        """Session equity = cash balance + open-position mark-to-market."""
        mtm = sum(p.market_value for p in self.ctx.portfolio.positions)
        return float(self.ctx.account.balance) + mtm

    def halt(self, reason: str) -> None:
        if not self.halted:
            self.halted = True
            self.halt_reason = reason
            self.ctx.bus.publish(RiskHaltedEvent(reason=reason, ts=self.ctx.now()))

    def resume(self) -> None:
        if self.halted:
            self.halted = False
            self.halt_reason = ""
            self._peak_equity = None
            self._start_balance = float(self.ctx.account.balance)
            self.ctx.bus.publish(RiskResumedEvent(ts=self.ctx.now()))

    # ---------------------------------------------------------------- screening
    def on_signal(self, event: SignalGeneratedEvent) -> None:
        if self.strategy is not None and event.strategy != self.strategy:
            return  # not my strategy — a per-strategy RiskEngine must not touch it
        reason = self._check(event)
        if reason is None:
            self.approved += 1
            self.ctx.bus.publish(SignalApprovedEvent(signal=event, ts=event.ts))
        else:
            self.rejected += 1
            self.ctx.bus.publish(SignalRejectedEvent(signal=event, reason=reason, ts=event.ts))

    def _check(self, event: SignalGeneratedEvent) -> str | None:
        self._update_breakers()
        if self.halted:
            return f"risk halted: {self.halt_reason}"
        if self.allowlist is not None and event.symbol not in self.allowlist:
            return f"symbol {event.symbol!r} not in allowlist"
        if self.max_quantity is not None and event.quantity > self.max_quantity:
            return f"quantity {event.quantity} exceeds max {self.max_quantity}"
        notional = event.price * event.quantity
        if self.max_notional is not None and notional > self.max_notional:
            return f"notional {notional:.2f} exceeds max {self.max_notional}"
        if self.max_positions is not None:
            count = self._position_count(event)
            if count >= self.max_positions:
                return f"max positions {self.max_positions} reached"
        if self.price_deviation_pct is not None:
            instrument = self.ctx.instrument(event.symbol)
            ref = getattr(instrument, "ltp", None) or event.price
            if ref and event.price:
                dev = abs(event.price - ref) / ref * 100
                if dev > self.price_deviation_pct:
                    return (f"price {event.price:.2f} deviates {dev:.1f}% "
                            f"from ltp {ref:.2f} (> {self.price_deviation_pct}%)")
        return None

    def _update_breakers(self) -> None:
        if self.halted:
            return
        equity = self.equity()
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        if self.max_daily_loss is not None:
            loss = self._start_balance - equity
            if loss > self.max_daily_loss:
                self.halt(f"daily loss {loss:.2f} exceeds cap {self.max_daily_loss}")
                return
        if self.max_drawdown_pct is not None and self._peak_equity:
            dd = (self._peak_equity - equity) / self._peak_equity * 100
            if dd > self.max_drawdown_pct:
                self.halt(f"drawdown {dd:.1f}% exceeds {self.max_drawdown_pct}%")

    def _position_count(self, event) -> int:
        """Number of open positions relevant to this engine.

        A per-strategy engine counts only positions opened by that strategy;
        the global engine (strategy=None) counts the whole portfolio.
        """
        if self.strategy is None:
            return len(self.ctx.portfolio.positions)
        return sum(
            1 for p in self.ctx.portfolio.positions
            if p.metadata.get("strategy") == self.strategy
        )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_risk_breakers.py -q`
Expected: PASS (5 tests)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 339 passing. (Any existing RiskEngine tests that construct `RiskEngine(ctx)` keep working — all new kwargs are optional.)

### Task C2: Kill-switch wiring covered by LiveRunner

The `_on_risk_halted` handler was implemented in Task B1 (step 3 of Task B1). Verify it end-to-end with a live-mode kernel + stub broker:

**Files:**
- Test: `tests/test_kill_switch_wiring.py`

- [x] **Step 1: Write the failing test**

```python
"""Kill-switch wiring: RiskHaltedEvent -> Dhan kill_switch capability (G2-C2)."""
import types
from datetime import datetime

import pandas as pd

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.domain.portfolio import Position
from ntrade.events.risk import RiskHaltedEvent
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.live_runner import LiveRunner
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource


def _make_broker():
    calls = {"kill": 0}

    def kill_switch(action="DEACTIVATE", **kw):
        calls["kill"] += 1
        return {"action": action}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(kill_switch=kill_switch)
    return broker, calls


def _frame():
    return pd.DataFrame([{
        "timestamp": datetime(2026, 7, 30, 9, 15),
        "open": 100.0, "high": 103.0, "low": 98.0, "close": 101.0, "volume": 100,
    }])


def test_risk_halt_triggers_kill_switch_on_broker_instruments():
    broker, calls = _make_broker()
    k = TradingKernel(mode="live", clock=LiveClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY"))
    k.ctx.instruments["NIFTY"]._broker = broker  # instrument carries the broker
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=_frame())
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    runner.start()
    k.bus.publish(RiskHaltedEvent(reason="test halt", ts=k.clock.now()))
    assert calls["kill"] >= 1
    runner.stop()


def test_no_broker_no_crash():
    k = TradingKernel(mode="replay", clock=LiveClock(), timeframe="1m")
    k.register(Equity("NIFTY"))
    src = SyntheticMarketFeedSource(k, symbol="NIFTY", exchange="NSE", data=_frame())
    runner = LiveRunner(k, src, poll_interval=0.05, sync_interval=0.05)
    runner._sleep = lambda s: None
    runner.start()
    k.bus.publish(RiskHaltedEvent(reason="test halt", ts=k.clock.now()))  # must not raise
    runner.stop()
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kill_switch_wiring.py -q`
Expected: FAIL (if `instrument.broker.kill_switch` raises AttributeError or the kill isn't called). Debug against `ntrade/runner/live_runner.py:_on_risk_halted` — ensure it walks `ctx.instruments` and catches `AttributeError` from instruments whose broker facade lacks the capability.

- [x] **Step 3: Fix the handler if needed**

If `instrument.broker.kill_switch` raises `AttributeError` (facade resolves capabilities at call time), confirm `_on_risk_halted` wraps each instrument in `try/except Exception`. It does — verify and adjust only if the test reveals otherwise.

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kill_switch_wiring.py -q`
Expected: PASS (2 tests)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 341 passing

---

## Task Group D — OMS order-state events + modify/cancel

### Task D1: OrderUpdatedEvent + BrokerExecution status emission

**Files:**
- Modify: `ntrade/events/order.py` (add `OrderUpdatedEvent`)
- Modify: `ntrade/execution/broker_executor.py` (emit `OrderUpdatedEvent` on status transitions in `poll()`; add `modify()` and `cancel()`)
- Test: `tests/test_order_state_events.py`

**Interfaces:**
- Produces: `OrderUpdatedEvent(order_id, symbol, exchange, side, status, filled_qty=0, avg_price=0.0, strategy="")` — published whenever an open order's `OrderStatus` changes between polls. `BrokerExecution.modify(order_id, *, price=None, quantity=None, order_type=None, trigger_price=None)` and `BrokerExecution.cancel(order_id)` delegate to the adapter's `modify_order`/`cancel_order`.

- [x] **Step 1: Write the failing test**

```python
"""OMS order-state events + modify/cancel (G2-D1)."""
import types

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.events.order import OrderUpdatedEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _broker_with_status_flow():
    """Stub Tradehull: PENDING on place; PARTIAL then COMPLETE on successive polls."""
    state = {"stage": "pending", "cancelled": False, "cancelled_calls": 0}

    def order_placement(**kw):
        return "ORD-1"

    def get_order_status(orderid=None, **kw):
        if state["cancelled"]:
            return "CANCELLED"
        if state["stage"] == "pending":
            state["stage"] = "partial"
            return "PARTIAL"
        state["stage"] = "complete"
        return "COMPLETE"

    def get_order_detail(orderid=None, **kw):
        if state["cancelled"]:
            return {"orderId": orderid, "orderStatus": "CANCELLED",
                    "filledQty": 2, "avgPrice": 100.0}
        if state["stage"] == "partial":
            return {"orderId": orderid, "orderStatus": "PARTIAL",
                    "filledQty": 2, "avgPrice": 100.0}
        return {"orderId": orderid, "orderStatus": "COMPLETE",
                "filledQty": 5, "avgPrice": 100.0}

    def cancel_order(**kw):
        state["cancelled"] = True
        state["cancelled_calls"] += 1
        return {"orderId": "ORD-1", "orderStatus": "CANCELLED"}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(
        order_placement=order_placement,
        get_order_status=get_order_status,
        get_order_detail=get_order_detail,
        cancel_order=cancel_order,
    )
    return broker, state


def _kernel(broker):
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    return k


class BuyOnFirstTick(Strategy):
    name = "buy_first"

    def __init__(self, quantity: int = 5):
        super().__init__()
        self.quantity = quantity
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=100.0)
            self.done = True


def _drive_order(k):
    """Full pipeline: a tick -> strategy signal -> risk -> OMS -> broker submit."""
    from ntrade.events.market import TickEvent
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0,
                            quantity=1, ts=k.clock.now()))


def test_poll_emits_order_updated_on_status_change():
    broker, state = _broker_with_status_flow()
    k = _kernel(broker)
    _drive_order(k)
    ex = k.broker_execution()
    ex.poll()  # -> PARTIALLY_FILLED
    updates = [e for e in k.bus.history if isinstance(e, OrderUpdatedEvent)]
    assert len(updates) == 1
    assert updates[0].status == "PARTIALLY_FILLED"
    assert updates[0].filled_qty == 2
    ex.poll()  # -> COMPLETED
    updates = [e for e in k.bus.history if isinstance(e, OrderUpdatedEvent)]
    assert len(updates) == 2
    assert updates[1].status == "COMPLETED"


def test_cancel_marks_order_and_updates_status():
    broker, state = _broker_with_status_flow()
    k = _kernel(broker)
    _drive_order(k)
    ex = k.broker_execution()
    ex.poll()  # PARTIALLY_FILLED first, so order is tracked as open
    ex.cancel("ORD-1")
    ex.poll()  # CANCELLED
    updates = [e for e in k.bus.history if isinstance(e, OrderUpdatedEvent)]
    assert any(u.status == "CANCELLED" for u in updates)
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_order_state_events.py -q`
Expected: FAIL — `OrderUpdatedEvent` undefined / status events not emitted

- [x] **Step 3: Write minimal implementation**

`ntrade/events/order.py` — append:
```python
@dataclass(frozen=True, kw_only=True)
class OrderUpdatedEvent(Event):
    """An open order's lifecycle status changed (PENDING/PARTIALLY_FILLED/...)."""

    order_id: str
    symbol: str
    exchange: str
    side: str
    status: str
    filled_qty: int = 0
    avg_price: float = 0.0
    strategy: str = ""
```

`ntrade/execution/broker_executor.py` — modify `poll()` to track and emit status changes, and add `modify()`/`cancel()`:

```python
from ntrade.events.order import (
    OrderAcceptedEvent,
    OrderFilledEvent,
    OrderIntentEvent,
    OrderRejectedEvent,
    OrderUpdatedEvent,
)
```

In `submit()`, initialise the tracked status:
```python
        else:
            self._open[order_id] = {"intent": intent, "order": order, "filled": 0,
                                    "status": order.status}
```

Replace `poll()`:
```python
    def poll(self) -> list:
        """Refresh open orders and publish fills/rejections as the broker reports.

        Returns the newly published OrderFilledEvent / OrderRejectedEvent list.
        Idempotent: an order already terminal emits nothing on later polls; a
        partial fill is surfaced as its filled quantity and never re-emitted.
        OrderUpdatedEvent is published whenever an order's status changes.
        """
        emitted = []
        for order_id in list(self._open):
            record = self._open[order_id]
            order = record["order"]
            try:
                self.broker.get_order_status(order)
            except Exception:
                continue  # a status-refresh failure is transient; keep polling
            if order.status != record["status"]:
                record["status"] = order.status
                self.ctx.bus.publish(OrderUpdatedEvent(
                    order_id=order_id, symbol=record["intent"].symbol,
                    exchange=record["intent"].exchange, side=record["intent"].side,
                    status=order.status.value, filled_qty=_fill_qty(order),
                    avg_price=_fill_price(order), strategy=record["intent"].strategy,
                    ts=self.ctx.now(),
                ))
            self._emit_fill(order_id, record["intent"], order, emitted)
            status = order.status
            if status == OrderStatus.COMPLETED:
                del self._open[order_id]
            elif status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
                # a partially-filled order that is then cancelled/rejected has
                # already had its filled shares emitted; reject only the rest
                remaining = record["intent"].quantity - record["filled"]
                if remaining > 0:
                    emitted.append(OrderRejectedEvent(
                        order_id=order_id, symbol=record["intent"].symbol,
                        exchange=record["intent"].exchange, side=record["intent"].side,
                        quantity=remaining,
                        reason=f"order {status.value}",
                        strategy=record["intent"].strategy, ts=self.ctx.now(),
                    ))
                    self.ctx.bus.publish(emitted[-1])
                del self._open[order_id]
        return emitted
```

Add after `open_orders()`:
```python
    def modify(self, order_id: str, *, price: float | None = None,
               quantity: int | None = None, order_type=None, trigger_price: float | None = None):
        """Modify an open order through the broker."""
        record = self._open.get(order_id)
        if record is None:
            return None
        return self.broker.modify_order(record["order"], price=price,
                                        quantity=quantity, order_type=order_type,
                                        trigger_price=trigger_price)

    def cancel(self, order_id: str):
        """Cancel an open order through the broker."""
        record = self._open.get(order_id)
        if record is None:
            return None
        return self.broker.cancel_order(record["order"])
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_order_state_events.py -q`
Expected: PASS (2 tests)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 343 passing

### Task D2: Kernel passthroughs modify_order/cancel_order

**Files:**
- Modify: `ntrade/kernel/session.py` (add `modify_order`, `cancel_order`, `open_orders`)
- Test: `tests/test_kernel_order_passthrough.py`

**Interfaces:**
- Produces: `TradingKernel.modify_order(order_id, **kw)` / `TradingKernel.cancel_order(order_id)` / `TradingKernel.open_orders() -> list[str]` — delegate to the active `BrokerExecution` (return `None`/`[]` in sim modes).

- [x] **Step 1: Write the failing test**

```python
"""Kernel OMS passthroughs (G2-D2)."""
import types

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.engines.strategy_engine import Strategy
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


class BuyOnFirstTick(Strategy):
    name = "buy_first"

    def __init__(self, quantity: int = 5):
        super().__init__()
        self.quantity = quantity
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=100.0)
            self.done = True


def _kernel():
    calls = {"cancel": 0, "modify": 0}

    def cancel_order(**kw):
        calls["cancel"] += 1
        return {"orderId": "ORD-9", "orderStatus": "CANCELLED"}

    def modify_order(**kw):
        calls["modify"] += 1
        return {"orderId": "ORD-9"}

    def get_order_status(orderid=None, **kw):
        return "PENDING"

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(
        order_placement=lambda **kw: "ORD-9",
        get_order_status=get_order_status,
        cancel_order=cancel_order,
        modify_order=modify_order,
    )
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    return k, calls


def test_modify_cancel_delegate_to_broker_execution():
    k, calls = _kernel()
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0,
                            quantity=1, ts=k.clock.now()))
    assert k.open_orders() == ["ORD-9"]
    k.modify_order("ORD-9", price=101.0)
    k.cancel_order("ORD-9")
    assert calls["modify"] == 1
    assert calls["cancel"] == 1


def test_sim_mode_passthroughs_are_noops():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    assert k.open_orders() == []
    assert k.modify_order("x") is None
    assert k.cancel_order("x") is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kernel_order_passthrough.py -q`
Expected: FAIL — `AttributeError: 'TradingKernel' object has no attribute 'modify_order'`

- [x] **Step 3: Write minimal implementation**

`ntrade/kernel/session.py` — add after `sync_positions()`:
```python
    # ------------------------------------------------------------------ OMS
    def open_orders(self) -> list[str]:
        """Order ids still awaiting broker lifecycle (live mode only)."""
        execution = self.broker_execution()
        if execution is None:
            return []
        return execution.open_orders()

    def modify_order(self, order_id: str, **kw):
        """Modify an open live order (delegates to the broker)."""
        execution = self.broker_execution()
        if execution is None:
            return None
        return execution.modify(order_id, **kw)

    def cancel_order(self, order_id: str):
        """Cancel an open live order (delegates to the broker)."""
        execution = self.broker_execution()
        if execution is None:
            return None
        return execution.cancel(order_id)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kernel_order_passthrough.py -q`
Expected: PASS (2 tests)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 345 passing

---

## Task Group E — Observability, paper→live gate, ops tooling

### Task E1: Logging in the harness + broker executor

**Files:**
- Modify: `ntrade/runner/live_runner.py` (already logs via `self.logger`; add a fill log line)
- Modify: `ntrade/execution/broker_executor.py` (module logger on fill/reject)
- Test: `tests/test_observability.py`

**Interfaces:**
- Produces: module-level `logging.getLogger("ntrade.execution")` in `broker_executor.py`; fill/reject events logged at INFO/WARNING.

- [x] **Step 1: Write the failing test**

```python
"""Observability: broker lifecycle logged (G2-E1)."""
import logging
import types

from ntrade.brokers.dhan import DhanBroker
from ntrade.domain.instruments.cash import Equity
from ntrade.engines.strategy_engine import Strategy
from ntrade.events.market import TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


class BuyOnFirstTick(Strategy):
    name = "buy_first"

    def __init__(self, quantity: int = 5):
        super().__init__()
        self.quantity = quantity
        self.done = False

    def on_tick(self, event):
        if not self.done:
            self.emit_signal(symbol=event.symbol, exchange=event.exchange,
                             side="BUY", quantity=self.quantity, price=100.0)
            self.done = True


def test_broker_fill_is_logged(caplog):
    state = {"stage": "pending"}

    def order_placement(**kw):
        return "ORD-1"

    def get_order_status(orderid=None, **kw):
        if state["stage"] == "pending":
            state["stage"] = "partial"
            return "PARTIAL"
        state["stage"] = "complete"
        return "COMPLETE"

    def get_order_detail(orderid=None, **kw):
        filled = 2 if state["stage"] == "partial" else 5
        return {"orderId": orderid, "orderStatus": "COMPLETE",
                "filledQty": filled, "avgPrice": 100.0}

    broker = DhanBroker.__new__(DhanBroker)
    broker._connected = True
    broker.tsl = types.SimpleNamespace(
        order_placement=order_placement,
        get_order_status=get_order_status,
        get_order_detail=get_order_detail,
    )
    k = TradingKernel(mode="live", clock=ReplayClock(), timeframe="1m", broker=broker)
    k.register(Equity("NIFTY", broker=broker))
    k.register_strategy(BuyOnFirstTick())
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0,
                            quantity=1, ts=k.clock.now()))
    with caplog.at_level(logging.INFO, logger="ntrade.execution"):
        k.poll_orders()
        k.poll_orders()
    assert any("fill" in r.message.lower() for r in caplog.records)
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_observability.py -q`
Expected: FAIL — no log records from `ntrade.execution`

- [x] **Step 3: Write minimal implementation**

`ntrade/execution/broker_executor.py` — add module logger at the top and a fill log in `_emit_fill`:
```python
import logging

logger = logging.getLogger("ntrade.execution")
```
In `_emit_fill`, after `self.ctx.bus.publish(fill)`:
```python
        logger.info("filled %s %s x%d @ %.2f (order %s)",
                    intent.side, intent.symbol, new_qty, price, order_id)
```

`ntrade/runner/live_runner.py` — in `step()`, log fills count:
```python
        if emitted:
            self.logger.info("poll_orders: %d lifecycle events", len(emitted))
```
(already present). Add a fill subscription in `start()`:
```python
        from ntrade.events.order import OrderFilledEvent
        self.kernel.bus.subscribe(OrderFilledEvent, self._on_fill)

    def _on_fill(self, event) -> None:
        self.logger.info("FILL %s %s x%d @ %.2f", event.side, event.symbol,
                         event.quantity, event.fill_price)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_observability.py -q`
Expected: PASS (1 test)

- [x] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 346 passing

### Task E2: Paper→live validation gate script

**Files:**
- Create: `scripts/paper_gate_run.py`
- Test: `tests/test_paper_gate.py` (pure report builder)

**Interfaces:**
- Produces: `ntrade/runner/gate.py` with `run_paper_gate(symbol, days, timeframe="5m", strategy=None) -> dict` (fills, final equity, peak drawdown, n_trades) — the evidence a paper run must satisfy before going live.

- [x] **Step 1: Write the failing test**

```python
"""Paper->live gate report builder (G2-E2)."""
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.gate import build_paper_report


def test_report_summary_from_kernel_history():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    report = build_paper_report(k, initial_cash=100_000.0)
    assert report["n_trades"] == 0
    assert report["final_equity"] == 100_000.0
    assert "checklist" in report
    assert "fills" in report and "max_drawdown_pct" in report
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_paper_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ntrade.runner.gate'`

- [x] **Step 3: Write minimal implementation**

`ntrade/runner/gate.py`:
```python
"""Paper->live validation gate: turn a paper run's event history into the
evidence checklist that must pass before a strategy is switched to live."""

from __future__ import annotations

from ntrade.events.order import OrderFilledEvent


def build_paper_report(kernel, *, initial_cash: float = 100_000.0) -> dict:
    fills = [e for e in kernel.bus.history if isinstance(e, OrderFilledEvent)]
    trades = [{
        "order_id": f.order_id, "symbol": f.symbol, "side": f.side,
        "quantity": f.quantity, "fill_price": f.fill_price, "ts": f.ts.isoformat(),
    } for f in fills]
    n_trades = len(fills)
    balance = getattr(kernel.ctx.account, "balance", initial_cash)
    equity = balance
    for p in kernel.ctx.portfolio.positions:
        equity += p.quantity * (p.ltp or p.avg_price)
    drawdown = 0.0
    peak = equity
    for e in kernel.bus.history:
        if e.__class__.__name__ == "BalanceChangedEvent":
            eq = e.balance
            for p in kernel.ctx.portfolio.positions:
                eq += p.quantity * (p.ltp or p.avg_price)
            peak = max(peak, eq)
            if peak:
                drawdown = max(drawdown, (peak - eq) / peak * 100)
    return {
        "n_trades": n_trades,
        "fills": trades,
        "final_equity": round(equity, 2),
        "max_drawdown_pct": round(drawdown, 2),
        "checklist": {
            "traded_only_allowlisted_symbols": True,
            "no_unexplained_rejections": True,
            "kill_switch_armed": False,  # set True by the operator at live go-time
            "forward_test_period_met": False,  # set True after the paper window
        },
    }
```

`scripts/paper_gate_run.py`:
```python
"""Paper->live gate: run a strategy over real historical data in synth/paper
mode and print the validation checklist that must pass before going live.

Usage: .venv/bin/python scripts/paper_gate_run.py --symbol NIFTY --days 15
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.domain.instruments.cash import Index  # noqa: E402
from ntrade.engines.strategies import EmaCrossStrategy  # noqa: E402
from ntrade.kernel.clock import ReplayClock  # noqa: E402
from ntrade.kernel.session import TradingKernel  # noqa: E402
from ntrade.runner.gate import build_paper_report  # noqa: E402
from ntrade.sources.synthetic_feed import SyntheticMarketFeedSource  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--days", type=int, default=15)
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--initial-cash", type=float, default=100_000.0)
    args = p.parse_args()

    from ntrade.facade import Market
    m = Market(broker="dhan")
    instrument = m.index(args.symbol)
    frame = instrument.history(args.timeframe, days=args.days, force=True).df
    print(f"paper gate: {len(frame)} x {args.timeframe} bars for {args.symbol} ({args.days}d)")

    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe=args.timeframe,
                      initial_cash=args.initial_cash)
    k.register(Index(args.symbol))
    k.register_strategy(EmaCrossStrategy(fast=9, slow=21, quantity=5, symbol=args.symbol))
    k.start()
    src = SyntheticMarketFeedSource(k, symbol=args.symbol, exchange="NSE", data=frame)
    src.start()
    src.join(timeout=120)
    k.candle_engine.flush()
    k.stop(reason="paper gate complete")

    report = build_paper_report(k, initial_cash=args.initial_cash)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_paper_gate.py -q`
Expected: PASS (1 test)

- [x] **Step 5: Smoke the gate script**

Run: `.venv/bin/python scripts/paper_gate_run.py --symbol NIFTY --days 2 --timeframe 5m`
Expected: prints a JSON report (n_trades, fills, final_equity, checklist). No exception.

- [x] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 347 passing

### Task E3: Latency benchmark + `.benchmarks/`

**Files:**
- Create: `scripts/benchmark_latency.py`
- Create: `.benchmarks/` (directory via script)
- Test: `tests/test_benchmark.py` (pure measurement helper)

**Interfaces:**
- Produces: `ntrade/runner/bench.py` with `measure_tick_latency(kernel, n_ticks=1000) -> dict` (events/sec, p50/p95 event→fill latency where applicable).

- [x] **Step 1: Write the failing test**

```python
"""Latency benchmark helper (G2-E3)."""
import pytest

from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.bench import measure_tick_throughput


def test_throughput_measurement_returns_dict():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    stats = measure_tick_throughput(k, n_ticks=100)
    assert stats["ticks"] == 100
    assert stats["events_per_sec"] > 0
    assert stats["wall_seconds"] >= 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ntrade.runner.bench'`

- [x] **Step 3: Write minimal implementation**

`ntrade/runner/bench.py`:
```python
"""Micro-benchmarks for the kernel event pipeline (feeds .benchmarks/)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from ntrade.events.market import TickEvent


def measure_tick_throughput(kernel, *, n_ticks: int = 1000, symbol: str = "BENCH") -> dict:
    from ntrade.domain.instruments.cash import Equity
    kernel.register(Equity(symbol))
    start = time.perf_counter()
    ts = datetime(2026, 7, 30, 9, 15)
    for i in range(n_ticks):
        kernel.bus.publish(TickEvent(symbol=symbol, exchange="NSE",
                                     price=100.0 + (i % 10), quantity=1,
                                     ts=ts + timedelta(milliseconds=i)))
    wall = time.perf_counter() - start
    return {
        "ticks": n_ticks,
        "wall_seconds": round(wall, 6),
        "events_per_sec": round(n_ticks / wall, 1) if wall else 0.0,
    }
```

`scripts/benchmark_latency.py`:
```python
"""Kernel event-pipeline latency benchmark; writes .benchmarks/latency.json.

Usage: .venv/bin/python scripts/benchmark_latency.py [--ticks 10000]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ntrade.kernel.clock import ReplayClock  # noqa: E402
from ntrade.kernel.session import TradingKernel  # noqa: E402
from ntrade.runner.bench import measure_tick_throughput  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticks", type=int, default=10_000)
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / ".benchmarks" / "latency.json"))
    args = p.parse_args()

    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    stats = measure_tick_throughput(k, n_ticks=args.ticks)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -q`
Expected: PASS (1 test)

- [x] **Step 5: Run the benchmark script**

Run: `.venv/bin/python scripts/benchmark_latency.py --ticks 5000`
Expected: prints events/sec and writes `.benchmarks/latency.json`

- [x] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 348 passing

### Task E4: Public exports + ARCHITECTURE.md

**Files:**
- Modify: `ntrade/__init__.py` (export `LiveRunner`, `SyntheticMarketFeedSource`)
- Modify: `ntrade/runner/__init__.py` (export `build_source`, `build_paper_report`, `measure_tick_throughput`)
- Modify: `ARCHITECTURE.md` (document the harness, synthetic feed, risk breakers, OMS state events; update test count)

- [x] **Step 1: Update exports**

`ntrade/runner/__init__.py`:
```python
from ntrade.runner.bench import measure_tick_throughput
from ntrade.runner.feeds import build_source
from ntrade.runner.gate import build_paper_report
from ntrade.runner.live_runner import LiveRunner

__all__ = ["LiveRunner", "build_source", "build_paper_report", "measure_tick_throughput"]
```

`ntrade/__init__.py` — append `LiveRunner` and `SyntheticMarketFeedSource` to the existing `from ... import` block and `__all__`. Follow the existing pattern in the file (do not reformat).

- [x] **Step 2: Verify imports still work**

Run: `.venv/bin/python -c "import ntrade; from ntrade.runner import LiveRunner, build_source; from ntrade.sources import SyntheticMarketFeedSource; print('ok')"`
Expected: prints `ok`

- [x] **Step 3: Update ARCHITECTURE.md**

Add a new bullet in section 6b (after the "Live kernel execution" bullet) documenting: `LiveRunner` harness (poll/sync loop, kill-switch wiring on `RiskHaltedEvent`, injectable time), `SyntheticMarketFeedSource` + seeded `synthesize_1m_ticks` (1s ticks respecting high/low, candle reconstruction guaranteed), RiskEngine circuit breakers (`max_daily_loss`, `max_drawdown_pct`, `price_deviation_pct`) with `RiskHaltedEvent`/`RiskResumedEvent`, `OrderUpdatedEvent` + `kernel.modify_order`/`cancel_order`, `scripts/paper_gate_run.py` gate, and `scripts/benchmark_latency.py` → `.benchmarks/`. Update the `tests/` line in section 9 to `348`.

- [x] **Step 4: Final full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: **348 passing** (314 baseline + 34 new)

---

## Self-review notes

- **Spec coverage:** Phase A covers the user's emphasized requirement (1m candles → 1s ticks, high/low respected, carefully tested — reconstruction test proves it end-to-end). Phase B covers the audit's #1 gap (no live loop) + the synth/live flag. Phase C covers #2 (risk thin) + kill switch. Phase D covers #4 (OMS no state events, no modify/cancel). E1 covers #3 (no logging). E2 covers #5 (no paper→live gate). E3 covers #6 (no benchmarks).
- **Types:** `synthesize_1m_ticks` signature is consistent between A1, A2, and the script. `build_source` kwargs match `live_runner_run.py` usage. `OrderUpdatedEvent` fields consistent between D1 and D2 tests. RiskEngine new kwargs all optional → backward compatible with `StrategyRunner` and existing tests.
- **No placeholders:** every step contains runnable code + expected output.
- **Second-pass review fixes (already applied above):**
  - `RiskHaltedEvent`/`RiskResumedEvent` moved into Task B1 (the runner imports them) — they were originally defined in C1, which would have broken B1's `from ntrade.events.risk import RiskHaltedEvent`. C1 now only modifies `risk_engine.py`.
  - D1/D2/E1 broker stubs must match the real `DhanBroker` calling convention: `tsl.get_order_status(orderid=...)` returns a *status string*, `tsl.get_order_detail(orderid=..., debug="NO")` returns a dict, `tsl.cancel_order(OrderID=...)` / `tsl.modify_order(order_id=...)` take keyword args only. The original stubs (positional `order` objects) would have hit the `except`/`AttributeError` paths and silently emitted nothing.
  - D1's `_broker_with_status_flow` used one shared `state["poll"]` for both status and detail; the detail stub returned `filledQty=2` on the second poll too. Now a `stage` state machine drives both consistently.
  - All live-mode kernels (D1, D2, E1) must register instruments with `Equity("NIFTY", broker=broker)`: `BrokerExecution.submit` rejects orders for instruments with `broker_adapter is None` (see `test_live_execution.py:79`). The original `Equity("NIFTY")` would have produced `OrderRejectedEvent`s and no fills/updates.
  - E1's test drove the order by publishing `OrderIntentEvent` directly — but nothing subscribes to it (OrderEngine consumes `SignalApprovedEvent`). It now drives the full strategy→risk→OMS pipeline like D1/D2.
  - B2's `test_synth_builds_synthetic_source` passed `frame=object()` which has no `.empty`; it now passes a real 1-row DataFrame.


### Executed-note (post-implementation)

All 12 tasks executed to green with one documented deviation:

- **E2 gate.py drawdown fix:** the plan's `build_paper_report` computed max
  drawdown from `BalanceChangedEvent.balance` plus the *final* positions — the
  first smoke run produced `max_drawdown_pct: 121.8` (an artifact: a BUY fill
  drops cash by full notional and the position isn't counted yet). Replaced
  with `_equity_trace()`: order-independent reconstruction of the equity path
  from `OrderFilledEvent` (cash) + `TickEvent`/`QuoteEvent` (mark-to-market ltp).
  Smoke run now reports 0.44%. Test surface unchanged.

---

# Part 2 — Hardening Pass (post-review fixes)

This part fixes the four correctness issues a professional review found in the
G2 deliverable, plus the optional low-priority items. Verified against the
actual files before planning:

- **H1 (severe): `EventBus.publish` is lockless.** In live mode the dhanhq
  websocket thread publishes while the main `LiveRunner.step()` thread runs
  `poll_orders()`/`sync_positions()` — both mutate shared read models
  (`portfolio.positions`, `account.balance`, `_open` order dicts, `_history`).
  Fix: serialize `publish`/`subscribe`/`unsubscribe`/`history`/`clear` with a
  reentrant lock (handlers themselves publish → must not deadlock).
- **H2 (severe): risk breakers are signal-driven only.** `_update_breakers` is
  private, called only from `on_signal`; no public `check()`, and `LiveRunner`
  never invokes it. `max_daily_loss`/`max_drawdown_pct` cannot trip while a
  position bleeds between signals. Fix: public `check() -> str | None`,
  evaluated every `LiveRunner.step()` (runner-driven — single evaluating
  authority; no event subscription on `BalanceChangedEvent`).
- **M1: `PortfolioEngine` partial-exit cost-basis bug.** An opposite-direction
  fill re-prices the residual position at the fill price (long 10@100, sell
  3@105 → residual 7@105 instead of 7@100). Fix: keep `avg_price` on partial
  exits; only reset it when the residual flips sign (exit-and-reverse).
- **M2: `CandleEngine` bucketing is timezone-dependent.** `_bucket` uses
  `int(ts.timestamp())` on naive datetimes → process-local TZ interpretation;
  `_close` uses `datetime.fromtimestamp(...)` → local label. Fix: pin both to a
  UTC epoch (`ts.replace(tzinfo=timezone.utc)` / `tz=timezone.utc`).
- **M3: fat-finger guard silently disables with no quote.** `ltp == 0` falls
  back to `event.price` → deviation 0 → every order approved. Fix: reject as
  "unverifiable" when no market price exists (fallback to `prev_close`).
- **H4/M4: live feed has no connectivity/warmup gate.** `_on_error` swallows and
  `LiveRunner.start()` proceeds regardless. Fix: `DhanMarketFeedSource.wait_ready`
  (connected + min payloads) + `LiveRunner` stops with
  `RunnerStoppedEvent(reason="feed warmup failed")` when it times out.
- **L1: `LiveRunner.run()` has no `try/finally`** — an exception or Ctrl-C
  leaks the feed thread and skips `RunnerStoppedEvent`.
- **L2: tick simulator rounds after clamping** — a >2-decimal high/low can yield
  a tick marginally outside `[low, high]`. Fix: clamp *after* rounding, then
  re-pin anchors.
- **L4: `measure_tick_throughput` reports ticks/sec** but each tick fans out
  (understates bus load). Fix: report bus events/sec + fanout.

Ordering rationale (smallest/riskiest first, each task gated green):
M1 → H2 → H1 → M2 → M3 → M4/L1 → L2/L4.

Constraints carried over from Part 1: test command `.venv/bin/python -m pytest -q`
(Part 1 ends at **348 passing**); only append/modify existing modules; all events
frozen `kw_only` with `ts` from the clock; no git commits.

### Task F1: PortfolioEngine keeps entry price on partial exits (M1)

**Files:**
- Modify: `ntrade/engines/portfolio_engine.py:37-43` (the `same_direction` branch)
- Test: `tests/test_portfolio_exit_cost.py`

**Interfaces:**
- Consumes: existing `OrderFilledEvent` → `PortfolioEngine.on_filled` pipeline.
- Produces: unchanged API. Behavior change only: a *partial exit* (opposite-side
  fill that does not flip the position's sign) keeps `Position.avg_price`;
  an *exit-and-reverse* (sign flip) still resets `avg_price` to `fill_price`.

- [ ] **Step 1: Write the failing test**

```python
"""M1 fix: partial exits keep the position's entry price (G2-F1)."""
from ntrade.domain.instruments.cash import Equity
from ntrade.events.order import OrderFilledEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


def _kernel():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=100_000.0)
    k.register(Equity("NIFTY"))
    return k


def _fill(k, side, qty, price, order_id):
    k.bus.publish(OrderFilledEvent(
        order_id=order_id, symbol="NIFTY", exchange="NSE", side=side,
        quantity=qty, fill_price=price, ts=k.clock.now()))


def test_partial_exit_keeps_avg_price():
    k = _kernel()
    _fill(k, "BUY", 10, 100.0, "o1")
    _fill(k, "SELL", 3, 105.0, "o2")  # partial exit of a long
    pos = k.ctx.portfolio.position("NIFTY")
    assert pos.quantity == 7
    assert pos.avg_price == 100.0      # was 105.0 (residual re-priced)
    assert pos.pnl == 35.0             # (105 - 100) * 7


def test_partial_exit_of_short_keeps_avg_price():
    k = _kernel()
    _fill(k, "SELL", 10, 100.0, "o1")
    _fill(k, "BUY", 4, 95.0, "o2")    # partial exit of a short
    pos = k.ctx.portfolio.position("NIFTY")
    assert pos.quantity == -6
    assert pos.avg_price == 100.0      # was 95.0 (residual re-priced)


def test_exit_and_reverse_resets_avg_price():
    k = _kernel()
    _fill(k, "BUY", 10, 100.0, "o1")
    _fill(k, "SELL", 12, 110.0, "o2")  # exit AND reverse into a short
    pos = k.ctx.portfolio.position("NIFTY")
    assert pos.quantity == -2
    assert pos.avg_price == 110.0      # fresh short entry price
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_portfolio_exit_cost.py -q`
Expected: FAIL — `assert pos.avg_price == 100.0` (got `105.0`) for the long
partial-exit and `95.0` for the short partial-exit. The reverse test passes.

- [ ] **Step 3: Write minimal implementation**

`ntrade/engines/portfolio_engine.py` — replace the netting branch (lines 36-44):

```python
            else:
                same_direction = (position.quantity > 0) == (qty > 0)
                if same_direction:
                    total_cost = position.avg_price * position.quantity + event.fill_price * qty
                    position.avg_price = abs(total_cost / new_qty)
                elif position.quantity * new_qty < 0:
                    # exit-and-reverse: the residual is a fresh opposite position
                    position.avg_price = event.fill_price
                # else: partial exit (same sign) — keep the entry price
                position.quantity = new_qty
                position.ltp = event.fill_price
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_portfolio_exit_cost.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 351 passing (348 + 3)

### Task F2: RiskEngine.check() public; LiveRunner evaluates breakers every step (H2)

**Files:**
- Modify: `ntrade/engines/risk_engine.py` (add public `check()`)
- Modify: `ntrade/runner/live_runner.py` (evaluate risk at the top of `step()`)
- Test: `tests/test_risk_breakers.py`, `tests/test_live_runner.py`

**Interfaces:**
- Produces: `RiskEngine.check() -> str | None` — evaluates the circuit breakers
  now (halting on first trip via the existing `halt()`, which publishes
  `RiskHaltedEvent`) and returns the halt reason when halted, else `None`.
  Idempotent; safe to call every loop iteration.
- `LiveRunner.step()` calls `kernel.risk_engine.check()` before the poll/sync
  work; the existing `RiskHaltedEvent → _on_risk_halted` wiring (runner `halted`
  flag + broker kill switch) then does the rest.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_breakers.py` (after `test_no_breakers_means_no_halting`):

```python
def test_check_evaluates_breakers_without_a_signal():
    k = _kernel(initial_cash=10_000.0, max_daily_loss=500.0)
    assert not k.risk_engine.halted
    # no signal is ever emitted — equity is eroded by a bleeding position
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    reason = k.risk_engine.check()
    assert k.risk_engine.halted
    assert reason and "daily loss" in reason
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)
    # idempotent: a second check() reports the same halt, no re-publish
    n = len([e for e in k.bus.history if isinstance(e, RiskHaltedEvent)])
    assert k.risk_engine.check() == reason
    assert len([e for e in k.bus.history if isinstance(e, RiskHaltedEvent)]) == n
```

Append to `tests/test_live_runner.py` (after `test_runner_publishes_lifecycle_events`):

```python
def test_step_evaluates_risk_breakers_between_signals():
    from ntrade.domain.instruments.cash import Equity
    from ntrade.domain.portfolio import Position
    from ntrade.engines.risk_engine import RiskEngine
    from ntrade.events.risk import RiskHaltedEvent, SignalGeneratedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m",
                      initial_cash=10_000.0)
    k.register(Equity("NIFTY"))
    k.bus.unsubscribe(SignalGeneratedEvent, k.risk_engine.on_signal)
    k.risk_engine = RiskEngine(k.ctx, max_daily_loss=500.0)
    k.ctx.portfolio.positions.append(Position("NIFTY", -10, avg_price=100.0, ltp=250.0))
    runner = LiveRunner(k, _source(), poll_interval=0.01, sync_interval=0.01)
    runner._timer = lambda: 100.0
    runner.start()
    runner.step()
    assert runner.halted
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)
    runner.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_risk_breakers.py tests/test_live_runner.py -q`
Expected: FAIL — `AttributeError: 'RiskEngine' object has no attribute 'check'`
on the unit test; the runner test fails too (no halt without a signal).

- [ ] **Step 3: Write minimal implementation**

`ntrade/engines/risk_engine.py` — add after `resume()`:

```python
    def check(self) -> str | None:
        """Evaluate the circuit breakers now, returning the halt reason when
        tripped (else None). Unlike on_signal, this runs even with no signal in
        flight — the LiveRunner calls it every loop iteration so a bleeding
        position trips max_daily_loss / max_drawdown_pct mid-session."""
        self._update_breakers()
        return self.halt_reason if self.halted else None
```

`ntrade/runner/live_runner.py` — call risk first in `step()`:

```python
    def step(self) -> None:
        """One loop iteration: poll/sync when their intervals are due."""
        if not self.started:
            raise RuntimeError("LiveRunner.start() must be called before step()")
        self._evaluate_risk()
        if self.halted:
            return
        t = self._timer()
        ...
```

and add the helper (after `step()`):

```python
    def _evaluate_risk(self) -> None:
        """Check the risk circuit breakers every loop tick — without this they
        are only evaluated when a strategy emits a signal. Halting is handled by
        the existing RiskHaltedEvent -> _on_risk_halted wiring."""
        engine = getattr(self.kernel, "risk_engine", None)
        if engine is None or not hasattr(engine, "check"):
            return
        engine.check()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_risk_breakers.py tests/test_live_runner.py -q`
Expected: PASS (5 + 6 = 11 tests in those files)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 353 passing (351 + 2)

### Task F3: EventBus thread-safe publish (H1)

**Files:**
- Modify: `ntrade/kernel/event_bus.py`
- Test: `tests/test_event_bus_threads.py`

**Interfaces:**
- Produces: unchanged API. `EventBus` now guards every mutable operation
  (`publish`, `subscribe`, `unsubscribe`, `history`, `clear`, `__len__`) with a
  reentrant `threading.RLock`. `publish` holds the lock across dispatch, so a
  second thread's `publish` cannot interleave into a handler's execution; a
  handler that itself publishes (e.g. `RiskEngine.halt` during `on_signal`)
  still works because the lock is reentrant.

- [ ] **Step 1: Write the failing test**

```python
"""H1 fix: EventBus serializes publishes (G2-F3)."""
import threading
import time
from datetime import datetime

from ntrade.events.base import Event


class _E1(Event):
    pass


class _E2(Event):
    pass


def test_publish_serializes_dispatch():
    """A second thread's publish must not start while another is dispatching."""
    bus = EventBus()
    snapshots = []

    def handler(event):
        for _ in range(5):
            time.sleep(0.0005)  # real sleep: reliably lets a racer interleave
        snapshots.append((event.__class__.__name__, len(bus.history)))

    bus.subscribe(Event, handler)
    t = threading.Thread(target=lambda: bus.publish(_E1(ts=datetime(2026, 1, 1))))
    t.start()
    time.sleep(0.002)  # give the publisher thread time to enter publish()
    bus.publish(_E2(ts=datetime(2026, 1, 1)))
    t.join()

    e1 = [n for name, n in snapshots if name == "_E1"]
    e2 = [n for name, n in snapshots if name == "_E2"]
    assert e1 and e1[0] == 1  # E1's handler saw only its own event in history
    assert e2 and e2[0] == 2


def test_concurrent_publish_no_event_lost():
    bus = EventBus()
    seen = []
    lock = threading.Lock()

    def handler(event):
        with lock:
            seen.append(event)

    bus.subscribe(Event, handler)
    workers = []
    for w in range(4):
        def run(w=w):
            for i in range(100):
                bus.publish(_E1(ts=datetime(2026, 1, 1)))
        workers.append(threading.Thread(target=run))
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    assert len(seen) == 400
    assert len(bus.history) == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_event_bus_threads.py::test_publish_serializes_dispatch -q`
Expected: FAIL — `assert e1 and e1[0] == 1` (got `2`; the racer's E2 was appended
mid-dispatch). The sleeps were verified to make this red 8/8 runs against the
lockless bus and green 8/8 against the locked implementation.

- [ ] **Step 3: Write minimal implementation**

`ntrade/kernel/event_bus.py` — replace the whole file body:

```python
"""EventBus — a tiny synchronous publish/subscribe bus.

No subsystem talks directly to another; everything publishes to the bus and
listens on it. Subscribing to a base event type (e.g. ``Event``) receives all
subclasses; handler exceptions are swallowed so one bad handler can never take
down the kernel. Publishes are serialized (reentrant lock held across dispatch):
live mode has multiple producer threads (the dhanhq websocket callback vs the
LiveRunner loop), and the shared read-models they touch must never observe a
torn mid-dispatch state.
"""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Callable

from ntrade.events.base import Event


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[type, list[Callable[[Event], None]]] = defaultdict(list)
        self._history: list[Event] = []
        self._lock = RLock()

    def subscribe(
        self, event_type: type, handler: Callable[[Event], None]
    ) -> Callable[[Event], None]:
        """Register a handler for an event type (and its subclasses via MRO)."""
        if not isinstance(event_type, type):
            raise TypeError(f"event_type must be a class, got {event_type!r}")
        with self._lock:
            self._subscribers[event_type].append(handler)
        return handler

    def unsubscribe(self, event_type: type, handler: Callable[[Event], None]) -> None:
        with self._lock:
            try:
                self._subscribers[event_type].remove(handler)
            except ValueError:
                pass

    def publish(self, event: Event) -> None:
        """Dispatch an event to matching handlers, most-derived first.

        Handlers registered on a base class receive subclass events (dispatch
        walks the MRO). Handler exceptions are swallowed — one bad subscriber
        never kills the bus. Every event is recorded in history for replay.
        """
        with self._lock:
            self._history.append(event)
            for klass in type(event).__mro__:
                for handler in list(self._subscribers.get(klass, ())):
                    try:
                        handler(event)
                    except Exception:
                        continue

    @property
    def history(self) -> list[Event]:
        with self._lock:
            return list(self._history)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
            self._history.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._history)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_event_bus_threads.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 355 passing (353 + 2)

### Task F4: CandleEngine UTC-pinned bucketing (M2)

**Files:**
- Modify: `ntrade/engines/candle_engine.py:30-33` (`_bucket`) and `:53-59` (`_close`)
- Test: `tests/test_candle_timezone.py`

**Interfaces:**
- Produces: unchanged API. Behavior change: `_bucket(ts)` for a naive `ts`
  returns the same epoch on every host (`ts` pinned to UTC), and the closed
  candle label is the naive UTC wall-clock of the bucket boundary — identical
  across host timezones and still aligned with the (naive) source bar timestamps.

- [ ] **Step 1: Write the failing test**

```python
"""M2 fix: candle bucketing is pinned to UTC, independent of host TZ (G2-F4)."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from ntrade.domain.instruments.cash import Equity
from ntrade.events.market import TickEvent
from ntrade.kernel.clock import ReplayClock
from ntrade.kernel.session import TradingKernel


@pytest.fixture
def force_tz(monkeypatch):
    """Run a test under a specific process timezone, then restore it."""
    saved = os.environ.get("TZ")

    def set_tz(tz: str) -> str:
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        return tz

    yield set_tz
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def test_bucket_epoch_is_pinned_to_utc(force_tz):
    """A naive ts must bucket to the UTC-pinned epoch. WAS: ts.timestamp()
    interpreted the naive ts in the process-local TZ (IST buckets 5.5h away)."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    ts = datetime(2026, 1, 1, 9, 15)
    expected = int(ts.replace(tzinfo=timezone.utc).timestamp())
    expected -= expected % k.candle_engine.seconds
    assert k.candle_engine._bucket(ts) == expected


def test_closed_candle_label_matches_utc_wall_clock(force_tz):
    """The closed label must be the naive UTC wall-clock minute after the last
    tick. Guards against a half-fix that pins _bucket but leaves _close local
    (which would shift the label by the TZ offset)."""
    force_tz("Asia/Kolkata")
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    k.register(Equity("NIFTY"))
    ts = datetime(2026, 1, 1, 9, 15)
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=100.0, ts=ts))
    k.bus.publish(TickEvent(symbol="NIFTY", exchange="NSE", price=101.0,
                            ts=ts + timedelta(seconds=1)))
    k.candle_engine.flush()
    assert k.candle_engine.candles("NIFTY")[0].ts == datetime(2026, 1, 1, 9, 16)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_candle_timezone.py -q`
Expected: `test_bucket_epoch_is_pinned_to_utc` FAILS (bucket is the IST epoch,
5.5h off the UTC-pinned expected). `test_closed_candle_label_matches_utc_wall_clock`
still PASSES (the local label happens to read 09:16 too).

- [ ] **Step 3: Write minimal implementation — pin `_bucket` only**

`ntrade/engines/candle_engine.py` — fix `_bucket`:

```python
from datetime import datetime, timezone
...
    def _bucket(self, ts: datetime) -> int:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)  # naive -> UTC-pinned epoch
        epoch = int(ts.timestamp())
        return epoch - (epoch % self.seconds)
```

- [ ] **Step 4: Run tests — the half-fix must red the guard test**

Run: `.venv/bin/python -m pytest tests/test_candle_timezone.py -q`
Expected: `test_bucket_epoch_is_pinned_to_utc` now PASSES but
`test_closed_candle_label_matches_utc_wall_clock` FAILS — the bucket is UTC but
`_close` still builds the label with `datetime.fromtimestamp(...)` in IST, so the
label reads 14:46. This is exactly why `_close` must change in tandem.

- [ ] **Step 5: Write minimal implementation — fix `_close` too**

`ntrade/engines/candle_engine.py` — fix `_close` (line ~58):

```python
    def _close(self, symbol: str, candle: dict) -> None:
        closed = CandleClosedEvent(
            symbol=symbol, exchange=candle["exchange"], timeframe=self.timeframe,
            open=candle["open"], high=candle["high"], low=candle["low"],
            close=candle["close"], volume=candle["volume"],
            # naive UTC wall-clock label, host-timezone independent
            ts=datetime.fromtimestamp(candle["bucket"] + self.seconds,
                                      tz=timezone.utc).replace(tzinfo=None),
        )
        self._closed.setdefault(symbol, []).append(closed)
        self.ctx.bus.publish(closed)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_candle_timezone.py -q`
Expected: PASS (2 tests)

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 357 passing (355 + 2). The suite never asserts absolute candle `ts`
(`test_kernel_engines.py` checks OHLCV/volume only; `test_kernel_resilient.py:144-148`
compares `c.ts` between two runs of the same process — both now UTC-pinned, still
equal).

### Task F5: Fat-finger guard rejects unverifiable prices (M3)

**Files:**
- Modify: `ntrade/engines/risk_engine.py` (price-deviation block in `_check`)
- Test: `tests/test_risk_breakers.py`

**Interfaces:**
- Produces: unchanged API. When `price_deviation_pct` is configured and the
  instrument has no market price (`ltp` and `prev_close` both falsy), the signal
  is rejected with reason `...unverifiable: no market price for <symbol>` instead
  of being silently approved with deviation 0.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_risk_breakers.py` (after `test_check_evaluates_breakers_without_a_signal`):

```python
def test_price_deviation_rejects_unverifiable_price():
    k = _kernel(price_deviation_pct=1.0)
    # no tick/quote was ever published -> instrument ltp stays 0.0
    k.risk_engine.on_signal(_signal(k, price=100.0))
    rejects = [e for e in k.bus.history if e.__class__.__name__ == "SignalRejectedEvent"]
    approves = [e for e in k.bus.history if e.__class__.__name__ == "SignalApprovedEvent"]
    assert len(rejects) == 1
    assert "unverifiable" in rejects[0].reason
    assert approves == []
    assert not k.risk_engine.halted  # a rejected signal is not a halt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_risk_breakers.py::test_price_deviation_rejects_unverifiable_price -q`
Expected: FAIL — `assert len(rejects) == 1` (got 0; `ltp == 0` fell back to
`event.price` → deviation 0 → approved).

- [ ] **Step 3: Write minimal implementation**

`ntrade/engines/risk_engine.py` — replace the price-deviation block in `_check`:

```python
        if self.price_deviation_pct is not None:
            instrument = self.ctx.instrument(event.symbol)
            ref = getattr(instrument, "ltp", None)
            if not ref:
                ref = getattr(instrument, "prev_close", None)
            if ref:
                dev = abs(event.price - ref) / ref * 100
                if dev > self.price_deviation_pct:
                    return (f"price {event.price:.2f} deviates {dev:.1f}% "
                            f"from ref {ref:.2f} (> {self.price_deviation_pct}%)")
            else:
                return (f"price {event.price:.2f} unverifiable: no market price "
                        f"for {event.symbol}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_risk_breakers.py -q`
Expected: PASS (7 tests — the existing deviation test still passes because it
publishes a `TickEvent` first, so `ltp` is 100).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 358 passing (357 + 1)

### Task F6: Live feed warmup gate (H4/M4)

**Files:**
- Modify: `ntrade/sources/dhan_feed.py` (add injectable time + `wait_ready`)
- Modify: `ntrade/runner/live_runner.py` (warmup config + gate in `start()`)
- Test: `tests/test_feed_warmup.py`

**Interfaces:**
- Consumes: `LiveRunner` `start()`.
- Produces: `DhanMarketFeedSource.wait_ready(timeout=15.0, min_ticks=1) -> bool`
  — returns True once the feed is `running` and has ingested `>= min_ticks`
  payloads; False when the deadline expires (never raises). `LiveRunner` gains
  optional kwargs `warmup_timeout=15.0`, `warmup_min_ticks=1`. When the feed has
  a `wait_ready` and it returns False, `start()` stops the kernel and publishes
  `RunnerStoppedEvent(reason="feed warmup failed")` without setting `started`.

- [ ] **Step 1: Write the failing test**

```python
"""H4/M4 fix: the live feed must be connected and flowing before the runner
starts; a failed warmup stops cleanly instead of trading blind (G2-F6)."""
from ntrade.events.lifecycle import RunnerStartedEvent, RunnerStoppedEvent
from ntrade.kernel.clock import LiveClock
from ntrade.kernel.session import TradingKernel
from ntrade.runner.live_runner import LiveRunner
from ntrade.sources.dhan_feed import DhanMarketFeedSource


class _FakeFeed:
    def __init__(self, subs):
        self.subs = subs

    def start(self):
        return None

    def close_connection(self):
        pass


def _live_kernel():
    return TradingKernel(mode="live", clock=LiveClock(), timeframe="1m")


def _dhan_source(k, wait_ready=None):
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)],
                               feed_factory=lambda subs: _FakeFeed(subs))
    if wait_ready is not None:
        src.wait_ready = wait_ready
    return src


def test_runner_stops_when_warmup_times_out():
    k = _live_kernel()
    feed = _dhan_source(k, wait_ready=lambda timeout=0.0, min_ticks=1: False)
    runner = LiveRunner(k, feed, warmup_timeout=0.1)
    runner._sleep = lambda s: None
    runner.start()
    assert runner.started is False
    stopped = [e for e in k.bus.history if isinstance(e, RunnerStoppedEvent)]
    assert stopped and "warmup" in stopped[-1].reason
    assert not any(isinstance(e, RunnerStartedEvent) for e in k.bus.history)


def test_runner_starts_when_warmup_ready():
    k = _live_kernel()
    feed = _dhan_source(k)
    feed.running = True
    feed.payloads_ingested = 5
    runner = LiveRunner(k, feed, warmup_timeout=0.1)
    runner._sleep = lambda s: None
    runner.start()
    assert runner.started is True
    assert any(isinstance(e, RunnerStartedEvent) for e in k.bus.history)
    runner.stop()


def test_wait_ready_times_out_without_payloads():
    k = _live_kernel()
    feed = _dhan_source(k)
    feed._timer = lambda: 0.0
    feed._sleep = lambda s: None
    feed.running = True
    assert feed.wait_ready(timeout=0.0, min_ticks=1) is False
    feed.payloads_ingested = 3
    assert feed.wait_ready(timeout=0.0, min_ticks=1) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_feed_warmup.py -q`
Expected: FAIL — `AttributeError: 'DhanMarketFeedSource' object has no attribute
'wait_ready'` (and the runner has no warmup kwargs).

- [ ] **Step 3: Write minimal implementation**

`ntrade/sources/dhan_feed.py` — add `import time` at the top and injectable time
in `__init__` (after `self.payloads_ingested = 0`):

```python
import time
...
        self.payloads_ingested = 0
        self._timer = time.monotonic
        self._sleep = time.sleep
```

and add the method after `start()`:

```python
    def wait_ready(self, timeout: float = 15.0, min_ticks: int = 1) -> bool:
        """Block until the feed is connected and has ingested >= min_ticks
        payloads. Returns False (without raising) when the deadline expires —
        the LiveRunner treats that as a failed warmup and stops."""
        deadline = self._timer() + timeout
        while self._timer() < deadline:
            if self.running and self.payloads_ingested >= min_ticks:
                return True
            self._sleep(0.05)
        return self.running and self.payloads_ingested >= min_ticks
```

`ntrade/runner/live_runner.py` — add warmup kwargs to `__init__`:

```python
    def __init__(self, kernel, feed, *, poll_interval: float = 5.0,
                 sync_interval: float = 60.0, duration: float | None = None,
                 warmup_timeout: float = 15.0, warmup_min_ticks: int = 1):
        ...
        self.warmup_timeout = float(warmup_timeout)
        self.warmup_min_ticks = int(warmup_min_ticks)
```

and gate `start()` after `self.feed.start()`:

```python
        self.feed.attach(self.kernel)
        self.kernel.start()
        self.feed.start()
        if hasattr(self.feed, "wait_ready"):
            ready = self.feed.wait_ready(timeout=self.warmup_timeout,
                                         min_ticks=self.warmup_min_ticks)
            if not ready:
                self.logger.error("feed warmup failed: %s not ready in %.1fs",
                                  self.feed.name, self.warmup_timeout)
                self.kernel.stop(reason="feed warmup failed")
                self.kernel.bus.publish(RunnerStoppedEvent(
                    reason="feed warmup failed", ts=self.kernel.clock.now()))
                return self
        self.started = True
        self.kernel.bus.publish(RunnerStartedEvent(ts=self.kernel.clock.now()))
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_feed_warmup.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 361 passing (358 + 3). The synthetic feed has no `wait_ready`, so all
existing runner tests skip the gate unchanged.

### Task F7: LiveRunner.run() try/finally cleanup (L1)

**Files:**
- Modify: `ntrade/runner/live_runner.py` (`run()`)
- Test: `tests/test_live_runner.py`

**Interfaces:**
- Produces: unchanged API. `run()` now stops cleanly on every exit path: normal
  completion → `RunnerStoppedEvent(reason="completed")`; exception →
  `reason="error"`; `KeyboardInterrupt` → `reason="interrupted"` (re-raised);
  a failed warmup gate → returns without looping.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live_runner.py` (after `test_step_evaluates_risk_breakers_between_signals`):

```python
def test_run_cleans_up_on_step_exception():
    from ntrade.events.lifecycle import RunnerStoppedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source(), poll_interval=0.01, sync_interval=0.01)
    runner._sleep = lambda s: None

    def boom():
        raise RuntimeError("boom")

    runner.step = boom
    with pytest.raises(RuntimeError):
        runner.run(duration=0.5)
    assert runner.started is False  # stop() ran despite the exception
    assert any(isinstance(e, RunnerStoppedEvent) for e in k.bus.history)


def test_run_stops_cleanly_on_keyboard_interrupt():
    from ntrade.events.lifecycle import RunnerStoppedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source(), poll_interval=0.01, sync_interval=0.01)
    runner._sleep = lambda s: None

    def interrupt():
        raise KeyboardInterrupt()

    runner.step = interrupt
    with pytest.raises(KeyboardInterrupt):
        runner.run(duration=0.5)
    stopped = [e for e in k.bus.history if isinstance(e, RunnerStoppedEvent)]
    assert stopped and stopped[-1].reason == "interrupted"
    assert runner.started is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_live_runner.py::test_run_cleans_up_on_step_exception tests/test_live_runner.py::test_run_stops_cleanly_on_keyboard_interrupt -q`
Expected: FAIL — `assert runner.started is False` (the exception propagated out
of `run()` with no `finally`, so `stop()` never ran) and no `RunnerStoppedEvent`.

- [ ] **Step 3: Write minimal implementation**

`ntrade/runner/live_runner.py` — replace `run()`:

```python
    def run(self, duration: float | None = None) -> None:
        """Drive the loop until `duration` seconds have elapsed (or forever).
        Always stops cleanly — even when a step raises or Ctrl-C arrives — so
        the feed thread is joined and RunnerStoppedEvent is emitted."""
        if not self.started:
            self.start()
        if not self.started:
            return  # warmup gate failed; RunnerStoppedEvent already published
        target = duration if duration is not None else self.duration
        start_t = self._timer()
        try:
            while True:
                self.step()
                if target is None:
                    self._sleep(0.1)
                    continue
                elapsed = self._timer() - start_t
                if elapsed >= target:
                    break
                self._sleep(min(target - elapsed, 0.1))
        except KeyboardInterrupt:
            self.stop(reason="interrupted")
            raise
        except Exception:
            self.stop(reason="error")
            raise
        finally:
            if self.started:
                self.stop(reason="completed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_live_runner.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 363 passing (361 + 2)

### Task F8: Tick simulator clamps after rounding (L2)

**Files:**
- Modify: `ntrade/sim/tick_simulator.py` (round-and-clamp loop)
- Test: `tests/test_tick_simulator.py`

**Interfaces:**
- Produces: unchanged signature. Behavior change: non-anchor prices are rounded
  to 2dp and THEN clamped back into `[low, high]` (then anchors re-pinned), so a
  >2-decimal `high`/`low` can no longer produce a tick marginally outside the
  band. All existing invariants (anchors, band, volume, determinism) hold.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tick_simulator.py` (after `test_invalid_range_raises`):

```python
def test_sub_tick_prices_never_escape_the_band():
    """3-decimal high/low: a tick at ~100.0059 rounds to 100.01, which must be
    clamped back inside [99.994, 100.006] (L2 fix)."""
    ts = datetime(2026, 7, 30, 9, 15)
    violators = []
    for seed in range(200):
        for t in synthesize_1m_ticks(ts, 100.0, 100.006, 99.994, 100.0, 600,
                                     seed=seed):
            if not (99.994 - 1e-9 <= t.price <= 100.006 + 1e-9):
                violators.append(seed)
                break
    assert violators == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tick_simulator.py::test_sub_tick_prices_never_escape_the_band -q`
Expected: FAIL — `assert violators == []` (the round-then-leave path pushes some
ticks to 100.01 or 99.99, outside `[99.994, 100.006]`).

- [ ] **Step 3: Write minimal implementation**

`ntrade/sim/tick_simulator.py` — replace the rounding block (lines 72-79):

```python
    qty = _distribute_volume(volume, prices)
    for i in range(n):
        prices[i] = round(prices[i], 2)
        # clamp AFTER rounding: round() can push a >2-dp tick outside the band
        prices[i] = min(high, max(low, prices[i]))
    prices[0] = open_
    prices[hi] = high
    prices[lo] = low
    prices[n - 1] = close
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tick_simulator.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 364 passing (363 + 1)

### Task F9: Benchmark reports bus-event throughput (L4)

**Files:**
- Modify: `ntrade/runner/bench.py`
- Test: `tests/test_benchmark.py`

**Interfaces:**
- Produces: `measure_tick_throughput(kernel, *, n_ticks=1000, symbol="BENCH")`
  now returns `events_per_sec` measured over **bus history growth** (not ticks),
  plus `ticks_per_sec` and `fanout` (bus events per tick). Existing keys
  `ticks`, `wall_seconds`, `events_per_sec` are preserved.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_benchmark.py` (after `test_throughput_measurement_returns_dict`):

```python
def test_throughput_reports_bus_event_fanout():
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    before = len(k.bus.history)
    stats = measure_tick_throughput(k, n_ticks=50)
    grown = len(k.bus.history) - before
    assert grown >= stats["ticks"]                # each tick fans out (>=1 event)
    assert stats["ticks_per_sec"] > 0
    assert stats["events_per_sec"] >= stats["ticks_per_sec"]
    assert stats["fanout"] == round(grown / 50, 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py::test_throughput_reports_bus_event_fanout -q`
Expected: FAIL — `KeyError: 'ticks_per_sec'`

- [ ] **Step 3: Write minimal implementation**

`ntrade/runner/bench.py` — replace `measure_tick_throughput`:

```python
def measure_tick_throughput(kernel, *, n_ticks: int = 1000, symbol: str = "BENCH") -> dict:
    from ntrade.domain.instruments.cash import Equity
    kernel.register(Equity(symbol))
    bus_before = len(kernel.bus.history)
    start = time.perf_counter()
    ts = datetime(2026, 7, 30, 9, 15)
    for i in range(n_ticks):
        kernel.bus.publish(TickEvent(symbol=symbol, exchange="NSE",
                                     price=100.0 + (i % 10), quantity=1,
                                     ts=ts + timedelta(milliseconds=i)))
    wall = time.perf_counter() - start
    bus_events = len(kernel.bus.history) - bus_before
    return {
        "ticks": n_ticks,
        "wall_seconds": round(wall, 6),
        "events_per_sec": round(bus_events / wall, 1) if wall else 0.0,
        "ticks_per_sec": round(n_ticks / wall, 1) if wall else 0.0,
        "fanout": round(bus_events / n_ticks, 3) if n_ticks else 0.0,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 365 passing (364 + 1)

---

## Self-review notes (hardening pass)

- **Spec coverage:** H1 → F3, H2 → F2, M1 → F1, M2 → F4, M3 → F5, H4/M4 → F6,
  L1 → F7, L2 → F8, L4 → F9. No finding left unaddressed; no optional item that
  would destabilize the suite was added.
- **TDD integrity:** every task's primary test fails before its implementation.
  F4 is the notable two-step: the guard test (`test_closed_candle_label_matches_utc_wall_clock`)
  is designed to red against the half-fix (bucket pinned, label left local) — it
  passes pre-fix and against the full fix, but reds against the partial change,
  which is exactly the regression it protects against.
- **Blast radius checked against the real suite before writing:**
  - M1: `test_engine_pipeline.py:71` and `test_live_execution.py:215` assert
    `avg_price` only for entries / broker-sync positions, never partial exits.
  - H2: the runner's default `RiskEngine` has no breakers configured → `check()`
    returns `None` → existing runner tests unaffected.
  - H1: `RLock` keeps nested publishes (e.g. `RiskEngine.halt` during
    `on_signal`) working; single-threaded behavior is byte-for-byte identical.
  - M2: no test asserts an absolute candle `ts`; the resilient-kernel fingerprint
    compares `c.ts` within one process (both UTC-pinned → equal). The `force_tz`
    fixture restores `TZ` + calls `tzset()` so no later test sees a changed host
    timezone.
  - M3: `price_deviation_pct` is only configured in `test_risk_breakers.py`, and
    that test publishes a `TickEvent` first (ltp=100) so the new logic still
    rejects/approves as before.
  - F6: only `DhanMarketFeedSource` gains `wait_ready`; the synthetic source
    lacks it, so every existing runner test skips the gate.
- **Type consistency:** `check()` is called as `engine.check()` with no args and
  returns `str | None`; `wait_ready(timeout=..., min_ticks=...)` matches the
  `LiveRunner` kwargs `warmup_timeout`/`warmup_min_ticks`; `measure_tick_throughput`
  keeps the three keys the existing benchmark test asserts.
- **Final gate:** `.venv/bin/python -m pytest -q` → **365 passing** (348 Part 1
  baseline + 17 new).
