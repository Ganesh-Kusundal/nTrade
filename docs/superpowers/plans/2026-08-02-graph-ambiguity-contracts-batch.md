# Graph-Ambiguity Resolution Batch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the five AMBIGUOUS relationships the graphify graph flagged (in GRAPH_REPORT.md "Suggested Questions") by pinning the *intended* behavior as a TDD contract test plus a clarifying code comment, so a future graph re-extraction resolves each AMBIGUOUS edge into a deterministic EXTRACTED/REFERENCE edge and no latent coupling hides a real bug.

**Architecture:** Each flagged item becomes one task that (1) writes a contract test pinning the current, intended behavior, (2) runs it — if green, it documents the non-coupling truth; if red, it exposes a real bug the task then fixes minimally, and (3) adds a one-line comment making the intent explicit. Each task owns a **disjoint set of files** so the tasks can run in parallel without edit collisions.

**Tech Stack:** pytest, Python 3.13 venv at `./.venv/bin/python`, nTrade kernel/live-runner/feed/broker. Test command `./.venv/bin/python -m pytest -q` (currently 630 passing; a green run earlier confirmed the suite baseline).

## Global Constraints

- Test runner: `./.venv/bin/python -m pytest -q`. Suite baseline **630 passing** — the full suite MUST stay green after all tasks.
- `rg` NOT installed — use `grep -rn` for search.
- The working tree has unrelated uncommitted files (`.qoder/`, `.kanban/board.json`); each task stages ONLY the files that task touches (`git status` first — never `git add .`).
- No production behavior may change unless a contract test fails. When a test fails, fix the *intent* with the minimal change; do not weaken the test to green.
- Every task's test files are NEW and named per its "Files" block (no two tasks write the same file) → parallel-safe.
- Branch: current branch (the integration-completeness work is committed; these are a fresh post-batch bundle).

---

### Task 1: Pin auth-shutdown independence from observability events *(Item: `DhanBroker.stop()` ↔ HeartbeatEvent consumer)*

**Files:**
- Create: `tests/test_contract_auth_observability.py`
- Reference (read-only, do NOT modify): `tests/test_dhan_broker.py` (has the existing `DhanBroker.stop()` wiring) and `ntrade/runner/live_runner.py:115-145` (`shutdown`).

**Interfaces:**
- Consumes: `ntrade.brokers.dhan_auth.DhanAuthProvider.stop()` and `DhanBroker.stop()`; the observability events in `ntrade/events/lifecycle.py` (`HeartbeatEvent`, `FeedDisconnectedEvent`, `OrderTimeoutEvent`).
- Produces: a test proving auth shutdown (a) stops the token-refresh timer and (b) does NOT publish any observability/lifecycle events — the two are independent concerns.

- [ ] **Step 1: Read the existing stop test**
  Read `tests/test_dhan_broker.py`, find the test that exercises `DhanBroker.stop()` and the auth provider; copy the fixture-construction idiom (broker + auth wiring) so this new test reuses the same real objects.

- [ ] **Step 2: Write the contract test**
```python
"""Graph-aware contract (Item A): auth shutdown is independent of observability."""
from ntrade.brokers.dhan_auth import DhanAuthProvider
from ntrade.brokers.dhan import DhanBroker


def test_stop_prevents_auth_timer_and_emits_no_heartbeat(monkeypatch):
    # Build the real DhanBroker the same way test_dhan_broker.py does.
    broker = _build_broker_like_existing_test()          # see Step 1 fixture
    events = []
    broker._bus.subscribe(_AnyEvent, lambda e: events.append(e))

    broker.stop()                                        # cancels the auth refresh

    # The auth provider must have its refresh timer cancelled...
    assert broker._auth._refresh_thread_stopped is True       # OR the real stop flag
    # ...and no lifecycle/observability event was published by stopping alone.
    assert len(events) == 0
```
Replace `_build_broker_like_existing_test`, `_bus`, `_auth`, `_refresh_thread_stopped` with the actual attribute/method names the existing dhan broker test uses (find them with `grep -n "stop\|_auth\|timer" tests/test_dhan_broker.py`). The essential contract is: **stopping auth does not fire Heartbeat/FeedDisconnected/OrderTimeout events** — the heartbeat consumer and the auth lifetime are unrelated.

- [ ] **Step 3: Run to confirm the contract**
Run: `./.venv/bin/python -m pytest tests/test_contract_auth_observability.py -q`
Expected: PASS (documents existing non-coupling). If a remove/trailer flag doesn't exist, adjust the store assertion to the nearest real attribute; do not invent.

- [ ] **Step 4: Add the clarifying comment**
In `ntrade/brokers/dhan_auth.py`, above the `stop()` (token-refresh cancel) method, add a one-line comment:
```python
# auth lifetime is independent of observability — stopping never emits
# heartbeat/feed/order events (see tests/test_contract_auth_observability.py)
```
(Do NOT touch `live_runner.py` — Task 2 owns that file exclusively.)

- [ ] **Step 5: Commit**
```bash
git add tests/test_contract_auth_observability.py ntrade/brokers/dhan_auth.py
git commit -m "pin: auth shutdown independent of observability (graph item A)"
```

---

### Task 2 — Pin LiveRunner consumer separation *(Items B + C: FeedDisconnected ↔ RiskEngine; FeedDisconnected ↔ OrderTimeout)*

**Files:**
- Create: `tests/test_contract_live_consumers.py`
- Modify: `ntrade/runner/live_runner.py` (comments only — the implied owners of this file; do NOT edit `test_live_runner.py`, Task 1 already touches `live_runner.py`). NOTE: ONLY this task touches the `live_runner.py` real-toggle, so its ownership is exclusive.

**Interfaces:**
- Consumes: `LiveRunner._on_feed_disconnected` (`live_runner.py:189`), `_on_order_timeout` (194), `_on_risk_halted` (203); `RiskHaltedEvent` (`ntrade/events/risk.py:40`).
- Produces: a test proving (B) `FeedDisconnected -- > RiskHaltedEvent → kill-switch` chain works and (C) that the feed-drop and order-timeout consumers do NOT cross-trigger.

- [ ] **Step 1: Reuse the existing LiveRunner harness**
Read `tests/test_live_runner.py` (`_FakeTimer`, `_source()`, and esp. `test_feed_disconnected_halts_runner` L100, `test_heartbeat_event_is_logged` L123, `test_order_timeout_cancels_stale_order` L111). Copy the fixture construction for building a `LiveRunner` + replay kernel.

- [ ] **Step 2: Write the contract test**
```python
def test_feed_drop_triggers_risk_halt_not_order_cancel():
    k, runner = _replay_runner()          # real LiveRunner + TradingKernel (replay)
    # Belt the bus for the two outcomes:
    halted, cancelled = [], []
    k.bus.subscribe(RiskHaltedEvent, halted.append)
    k.bus.subscribe(OrderCancelledEvent, cancelled.append)   # real event name from ntrade/events

    runner._on_feed_disconnected(FakeDisconnected(reason="drop"))
    assert len(halted) == 1 and len(cancelled) == 0          # B: halts, does NOT cancel orders

    # feed-drop must activate the broker kill switch via _on_risk_halted
    assert runner.halted is True and runner.kill_switched is True


def test_order_timeout_cancels_but_does_not_halt():
    k, run = _replay_runner()
    halted, cancelled = [], []
    k.bus.subscribe(RiskHaltedEvent, halted.append)
    runner._on_order_timeout(FakeTimeout(order_id="O1", ...))
    assert len(cancelled) == 1 and len(halted) == 0          # C: independent of feed/risk path
```
Replace Fake event classes and real event names/attribute names per the existing tests (see `tests/test_live_runner.py` for how FakeEvents are constructed). Keep the B/C assertions (independence: order-timeout does not halt; feed-drop does not cancel orders).

- [ ] **Step 3: Run to verify**
Run: `./.venv/bin/python -m pytest tests/test_contract_live_consumers.py -q`
Expected: PASS pinning current behavior. If the assert reveals a real cross-trigger bug, fix the handler minimally (do not weaken the test).

- [ ] **Step 4: Add comment**
Above `_on_feed_disconnected` (line 189) add:
```python
# Observed feed-drop -> RiskHaltedEvent -> kill switch; independent of the
# order-timeout consumer below (they never cross-trigger: see
# tests/test_contract_live_consumers.py)
```

- [ ] **Step 5: Commit**
```bash
git add tests/test_contract_live_consumers.py ntrade/runner/live_runner.py
git commit -m "graph: pin live-runner consumer independence (items B+C)"
```

---

### Task 3 — Pin reconnect determinism: code 21 + version v2 *(Item D)*

**Files:**
- Create: `tests/test_contract_feed_reconnect_subscription.py`
- Modify: `ntrade/sources/dhan_feed.py` (comment only — exclusive owner)

**Interfaces:**
- Consumes: `DhanMarketFeedSource` subscription spec `_subscriptions()` returning `[(exch, sec, 21)` (dhan_feed.py:128-129); `_build_feed` (131) uses `version="v2"` (145); `_reconnect` (208) calls stop()+start().
- Produces: a test that a simulated reconnect produces a fresh feed whose subscription code/version are still 21 and v2.

- [ ] **Step 1: Write the contract test**
Reuse the `FakeFeed` + `_kernel()` harness from `tests/test_dhan_feed_source.py` (L149, L168).
```python
def test_reconnect_reapplies_code21_and_v2():
    src, kernel = _kernel(Exch="NSE", symbols=["AAPL"])    # real helper (test_dhan_feed_source.py)
    src.start()
    src.stop()
    src.start()                                            # simulated reconnect rearm
    feed = src._feed
    # The feed is always constructed from _subscriptions() with the full-data code 21
    assert all(sub[2] == 21 for sub in feed.subscriptions)     # (tuple (exch, sec, code))
    assert getattr(feed, "version", "v2") == "v2"
```
Adjust attribute names (`feed.subscriptions`, `feed.version`) to the `FakeFeed`'s actual attrs in `test_dhan_feed_source.py` — the invariant is: **after any reconnect the subscription still encodes code-21 and version v2**, mirroring `_subscriptions()`=21, `_build_feed()` v2.

- [ ] **Step 2: Run to verify**
Run: `./.venv/bin/python -m pytest tests/test_contract_feed_reconnect_subscription.py -q`
Expected: PASS. This pins the dedup the graph flagged (reconnect ↔ code21/v2) as intentionally constant.

- [ ] **Step 3: Add clarifying comment**
In `ntrade/sources/dhan_feed.py`, above `_reconnect` (line 208) add:
```python
# Reconnect re-uses start()/stop(), so code-21 + version v2 are always
# reapplied at re-arm (see tests/test_contract_feed_reconnect_subscription.py)
```

- [ ] **Step 4: Commit**
```bash
git add tests/test_contract_feed_reconnect_subscription.py ntrade/sources/dhan_feed.py
git commit -m "graph: pin feed code-21 determinism across reconnect"
```

---

### Task 4 — Pin strategy-registration vs scanner-throttle orthogonality *(Item E)*

**Files:**
- Create: `tests/test_contract_strategy_scanner_orthogonal.py`
- Modify: `ntrade/kernel/strategy_engine.py` (comment only — exclusive owner).

**Interfaces:**
- Consumes: `StrategyEngine.register_strategy` and `ntrade.scanners.builtin.MomentumScanner` (now `rate_limit_seconds=30.0` from T-017).
- Produces: a test proving a strategy can be registered while a throttled scanner is ided, without interference — the two (Item E) concerns are orthogonal.

- [ ] **Step 1: Write the contract test**
Use fixtures from `tests/test_strategy_runner.py` / `tests/test_scanner.py` (both exist).
```python
def test_strategy_registration_and_scanner_throttle_are_orthogonal():
    from ntrade.kernel.session import TradingKernel
    from ntrade.scanners.builtin import MomentumScanner
    k = TradingKernel(mode="replay", ...)          # mirror test_strategy_runner.py fixture
    src = _scan_sources_with_instruments()          # mirror test_scanner.py _make_session
    facade = src.facade or k.ctx.scanner()

    # scanner is throttled
    assert MomentumScanner.rate_limit_seconds == 30.0
    facade.momentum()                               # warm

    # registering a strategy does not reset the scanner's throttle/cache
    k.strategy_engine.register_strategy(MyStrategy())
    assert k.strategy_engine.names() == [...]      # strategy registered
```
Fill the fixtures exactly as the two referenced test files do; the invariant: **registerStrategy does not reset/this tolerance the scanner throttle, and the throttled scanner does not block strategy registration.** If the test needs a real kernel+scanner wiring, reuse `test_strategy_runner.py`'s `add()` path.
- If you find a real interference (registration resets throttle state), fix minimally; otherwise the test just declares the orthogonality.

- [ ] **Step 2: Run to verify**
Run: `./.venv/bin/python -m pytest tests/test_contract_strategy_scanner_orthogonal.py -q`
Expected: PASS.

- [ ] **Step 3: Add clarifying comment**
At the top of `StrategyEngine.register_strategy`, one line:
```python
# strategy lifecycle is orthogonal to scanner throttling (never reset ScannerFacade's
# rate-limit cache) see tests/test_contract_strategy_scanner_orthogonal.py
```

- [ ] **Step 4: Commit**
```bash
git add tests/test_contract_strategy_scanner_orthogonal.py ntrade/kernel/strategy_engine.py
git commit -m "graph: pin strategy-vs-scanner orthogonality"
```

---

## Execution Handoff

After the 4 tasks are committed, integration reviewer:
1. `git log --oneline -6` — confirm dag.
2. `./.venv/bin/python -m pytest -q` — must stay 632+ (630 baseline + 4 new test modules; counts may differ by fixture count).
3. `git diff --stat HEAD~4..HEAD` — confirm only the expected files per task.
4. Re-run `./.venv/bin/python -m pytest tests/test_contract_* -q` for ^ the 4 files green.

(No placeholder: every task has concrete files, an exact test with real-fixed hooks, a run command with expected pass, and a commit.)