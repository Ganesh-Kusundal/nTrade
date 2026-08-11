# Task 6 (T-018): Wire feed watchdog reconnection on disconnect

**Goal:** The audit found D-013: `DhanMarketFeedSource._on_error` is a literal `pass` — a websocket error kills the feed silently with no reconnection, and a single blip ends the day (R-004). Wire reconnection so a drop re-subscribes.

## Facts verified from source (read these before coding)
- `ntrade/sources/dhan_feed.py` `_on_error(self, instance, error)` currently only logs (lines 215-217). It is invoked by dhanhq as `_on_error(feed, error)` (2 positional args).
- `_on_close` (lines 218-227) ALREADY publishes `FeedDisconnectedEvent`. Do not double-publish in a way that breaks it; keep `_on_close` as-is.
- Task 3 already added `self._reconnect_limiter = RateLimiter(calls_per_second=0.5)` in `__init__` and gates `start()` with `self._reconnect_limiter.wait()`. Reuse THAT limiter name (`_reconnect_limiter`) — do NOT create a new one.
- `self.kernel` may be `None` (a feed built without a kernel, e.g. `test_source_default_mode_is_full`). `self.bus` is `self.kernel.bus if self.kernel is not None else None` (base). Guard all kernel/bus access.
- `self.stop()` (194-204) tears down and clears the feed; `self.start()` (175-181) builds a fresh feed and subscribes. dhanhq MarketFeed is single-use: restart == fresh `start()`.
- `LiveStream.notify_reconnect()` ALREADY exists in `ntrade/domain/market/stream.py:128` (sets SUBSCRIBED + emits "reconnect"). It is reachable from the feed via `self.kernel.ctx.instruments_snapshot()` + `inst._stream`. Do NOT modify stream.py unless the implementer finds it genuinely broken (report if so).
- Feed obtains timestamps via `self.kernel.clock.now()` (see `_on_message` / `_on_close`).

## Implementation (in dhan_feed.py)
Add a `_reconnect_called` flag in `__init__` (`self._reconnect_called = False`), next to the other flags.

Replace `_on_error` (keep the same 2-arg dhanhq signature `(self, instance, error)`):
```python
def _on_error(self, instance, error) -> None:
    _logger.error("feed error: %s", error)
    if self.kernel is not None:
        self.bus.publish(FeedDisconnectedEvent(
            reason=str(error), ts=self.kernel.clock.now()))
    self._reconnect()

def _reconnect(self) -> None:
    self._reconnect_limiter.wait()
    try:
        self.stop()          # tear down the dead socket
        self.start()         # re-attach + re-subscribe (fresh MarketFeed)
        self._reconnect_called = True
        # Re-arm every instrument stream so consumers see them as live again.
        if self.kernel is not None:
            for instrument in self.kernel.ctx.instruments_snapshot():
                stream = getattr(instrument, "_stream", None)
                if stream is not None:
                    stream.notify_reconnect()
    except Exception as exc:
        _logger.error("reconnect failed — feed remains down: %s", exc)
```
Keep `_on_close` unchanged. Ensure `FeedDisconnectedEvent` is already imported (it is; used by `_on_close`).

## Tests (in tests/test_dhan_feed_source.py, matching the file's patterns)
`FakeFeed` and `_kernel()` already exist. Add:
```python
def test_feed_reconnects_after_disconnect():
    k = _kernel()
    builds = []
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: builds.append(subs) or FakeFeed(subs))
    src.start()
    before = len(builds)
    src._on_error(None, RuntimeError("ws dropped"))  # dhanhq 2-arg signature
    assert src._reconnect_called
    assert len(builds) > before      # the feed was actually rebuilt
    assert src.running                # and is running again
```
Also add a second test asserting that error → disconnect → reconnect publishes a FeedDisconnectedEvent and that a stream is back to subscribed:
```python
def test_reconnect_publishes_disconnect_and_resubscribes():
    from ntrade.events.lifecycle import FeedDisconnectedEvent
    k = _kernel()
    src = DhanMarketFeedSource(k, symbols=[(1, 2885)], symbol_map=SYMBOL_MAP,
                               feed_factory=lambda subs: FakeFeed(subs))
    src.start()
    inst = k.ctx.instrument("RELIANCE")
    src._on_error(None, RuntimeError("ws closed"))
    assert any(isinstance(e, FeedDisconnectedEvent) for e in k.bus.history)
    assert inst._stream.state.value == "SUBSCRIBED"   # adjust to actual enum
```
Check the actual `SubscriptionState` enum value string / attribute before hard-coding; if the state attr differs, assert the correct thing (e.g. `inst._stream.is_subscribed is True`). Keep both tests robust and green.

## Verification
```
./.venv/bin/python -m pytest -q
```
Expected: 630 (baseline) + the new tests = 632 (or reported number).

## Commit
```
git add ntrade/sources/dhan_feed.py tests/test_dhan_feed_source.py
git commit -m "T-018 wire feed watchdog reconnect on disconnect"
```
Stage ONLY the files you changed (dhan_feed.py + test_dhan_feed_source.py). Commit subject exactly `T-018 wire feed watchdog reconnect on disconnect`. The working tree has unrelated uncommitted files — do NOT stage them.

## Report
Write to `.superpowers/sdd/briefs/task-6-report.md`: commit hash, `git show --stat`, full suite count, and a note confirming whether `ntrade/domain/market/stream.py` required any change (it should not, since `notify_reconnect` already exists).