# Task 5 (T-014): Attach consumers for R-006 observability events in LiveRunner

**Goal:** Give R-004/R-005/R-006 events real consumers so none publish to zero subscribers. LiveRunner already publishes `HeartbeatEvent` (`_emit_heartbeat_if_due`) and watches the feed (`_check_feed_watchdog`), but nothing consumes `HeartbeatEvent`/`FeedDisconnectedEvent`/`OrderTimeoutEvent`. Attach handlers that (a) log heartbeats, (b) trip a risk halt on feed-disconnect, (c) cancel a stale PENDING order on timeout.

Do all work in `ntrade/runner/live_runner.py`. Do NOT touch risk_engine or broker_executor unless a hard blocker forces it (report if so).

## Context facts (verified — READ these before coding)
- `LiveRunner.__init__` already subscribes `RiskHaltedEvent → self._on_risk_halted` and `OrderFilledEvent → self._on_fill` (lines 50-52). Add three more `subscribe` calls + three handlers.
- `self.kernel` is a `TradingKernel` with `.bus`, `.clock`, `.cancel_order(order_id)` (ntrade/kernel/session.py:186), `.open_orders()`. So `_on_order_timeout` can call `self.kernel.cancel_order(event.order_id)` directly.
- `self.logger` is the module logger (`logging.getLogger("ntrade.runner")`).
- Events live in: `ntrade.events.lifecycle.HeartbeatEvent(tick_count, open_orders)`, `.FeedDisconnectedEvent(reason)`; `ntrade.events.order.OrderTimeoutEvent(order_id, symbol, exchange, side, quantity, age_seconds, ...)`; `ntrade.events.risk.RiskHaltedEvent(reason, ts)`.
- There is NO `_make_runner()` helper in tests/test_live_runner.py — those tests build `TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")` + `LiveRunner(k, _source())`. Write any new tests in that same style.
- The plan's suggested tests are WRONG for this file: they reference `_make_runner()` (doesn't exist) and monkeypatch `runner._on_feed_disconnected`/`runner._on_order_timeout` after construction — but the bus captured the **bound method at subscribe-time**, so a later attribute rebind does NOT change what gets dispatched. Use the corrected tests in the Brief's "Tests" section instead.

## Implementation

### subscribe (in `__init__`, after the existing OrderFilledEvent subscribe)
Add top-of-file imports:
```python
from ntrade.events.lifecycle import HeartbeatEvent, FeedDisconnectedEvent, RunnerStartedEvent, RunnerStoppedEvent
from ntrade.events.order import OrderFilledEvent
from ntrade.events.risk import RiskHaltedEvent
```
Adjust the existing `from ntrade.events.lifecycle import ...` line 15 and the local `from ntrade.events.order import OrderFilledEvent` (line 51) to consolidate. Then:
```python
self.kernel.bus.subscribe(HeartbeatEvent, self._on_heartbeat)
self.kernel.bus.subscribe(FeedDisconnectedEvent, self._on_feed_disconnected)
self.kernel.bus.subscribe(OrderTimeoutEvent, self._on_order_timeout)
```
(Import `OrderTimeoutEvent` where needed.)

### Add three handlers (place near `_on_fill` / `_on_risk_halted`)
```python
def _on_heartbeat(self, event) -> None:
    self.logger.info("heartbeat tick_count=%d open_orders=%d",
                     event.tick_count, event.open_orders)

def _on_feed_disconnected(self, event) -> None:
    self.logger.warning("feed disconnected: %s — halting", event.reason)
    self.kernel.bus.publish(RiskHaltedEvent(
        reason=f"feed disconnected: {event.reason}", ts=self.kernel.clock.now()))

def _on_order_timeout(self, event) -> None:
    self.logger.warning("order timeout: %s %s x%d aged %.0fs — cancelling",
                        event.side, event.symbol, event.quantity, event.age_seconds)
    self.kernel.cancel_order(event.order_id)
```
Do not import `RiskHaltedEvent` twice. `_on_order_timeout` cancel goes through the kernel (order reconciliation stays on the broker/tsl path; the kernel.cancel_order already delegates appropriately — do not change that path).

## Tests (tests/test_live_runner.py) — use these CORRECT versions
Append to the file (import `TradingKernel`, `ReplayClock`, `LiveRunner`, `_source` all already available):
```python
def test_feed_disconnected_halts_runner():
    from ntrade.events.lifecycle import FeedDisconnectedEvent
    from ntrade.events.risk import RiskHaltedEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    runner.kernel.bus.publish(FeedDisconnectedEvent(
        reason="ws drop", ts=runner.kernel.clock.now()))
    assert runner.halted
    assert any(isinstance(e, RiskHaltedEvent) for e in k.bus.history)


def test_order_timeout_cancels_stale_order():
    from ntrade.events.order import OrderTimeoutEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    cancelled = []
    runner.kernel.cancel_order = lambda oid: cancelled.append(oid)
    runner.kernel.bus.publish(OrderTimeoutEvent(
        order_id="O1", symbol="TCS", exchange="NSE", side="BUY",
        quantity=10, age_seconds=120.0, ts=runner.kernel.clock.now()))
    assert cancelled == ["O1"]


def test_heartbeat_event_is_logged(caplog):
    import logging
    from ntrade.events.lifecycle import HeartbeatEvent
    k = TradingKernel(mode="replay", clock=ReplayClock(), timeframe="1m")
    runner = LiveRunner(k, _source())
    with caplog.at_level(logging.INFO, logger="ntrade.runner"):
        k.bus.publish(HeartbeatEvent(tick_count=5, open_orders=2, ts=k.clock.now()))
    assert "tick_count=5" in caplog.text and "open_orders=2" in caplog.text
```
If `k.bus.history` does not record broadcast events (verify), fall back to subscribing a collecting callback, but confirm first — the existing `test_step_evaluates_risk_breakers_between_signals` reads `k.bus.history`, so it should work. Each test must actually exercise the real handler (not a rebind), so do NOT monkeypatch `runner._on_*`. `test_heartbeat_event_is_logged` requires pytest `caplog` — confirm pytest-capture has `caplog` (it is built into pytest).

## Verify
```
./.venv/bin/python -m pytest -q
```
Expected: 627 passing (baseline) + 3 new = 630.

## Commit
```
git add ntrade/runner/live_runner.py tests/test_live_runner.py
git commit -m "T-014 attach consumers for heartbeat/feed-drop/order-timeout events"
```
Stage only files you changed. Subject exactly `T-014 attach consumers for heartbeat/feed-drop/order-timeout events`.

## Report
Write `.superpowers/sdd/briefs/task-5-report.md`: commit hash, `git show --stat`, full-suite count, and a note if you had to touch any file beyond live_runner.py/test_live_runner.py (and why).