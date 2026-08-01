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
