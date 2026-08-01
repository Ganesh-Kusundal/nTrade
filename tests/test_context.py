"""TradingContext — thread-safety and basic API tests."""

import threading

from ntrade.kernel.clock import LiveClock
from ntrade.kernel.context import TradingContext
from ntrade.kernel.event_bus import EventBus


def _ctx():
    return TradingContext(EventBus(), LiveClock(), mode="live")


def test_context_has_reentrant_lock():
    ctx = _ctx()
    assert hasattr(ctx, "lock")
    assert isinstance(ctx.lock, type(threading.RLock()))


def test_register_and_instrument():
    from ntrade.domain.instruments.cash import Equity
    ctx = _ctx()
    eq = Equity("TCS")
    ctx.register(eq)
    assert ctx.instrument("TCS") is eq


def test_instruments_snapshot():
    from ntrade.domain.instruments.cash import Equity
    ctx = _ctx()
    ctx.register(Equity("A"))
    ctx.register(Equity("B"))
    snap = ctx.instruments_snapshot()
    assert len(snap) == 2
    symbols = {i.symbol for i in snap}
    assert symbols == {"A", "B"}


def test_concurrent_register_and_read():
    """Concurrent register + instrument() from multiple threads — no crash."""
    from ntrade.domain.instruments.cash import Equity
    ctx = _ctx()
    errors = []

    def writer(start):
        try:
            for i in range(50):
                ctx.register(Equity(f"S{start}_{i}"))
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(50):
                ctx.instrument("S0_0")
                ctx.instruments_snapshot()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "thread deadlocked under the context lock"

    assert errors == [], f"Thread safety errors: {errors}"
    assert len(ctx.instruments_snapshot()) == 200


def test_barrier_storm_snapshot_never_raises():
    """Deterministic Barrier storm: all threads start simultaneously so the
    dict-size-change race actually trips if the lock is ever removed.

    Without the lock, ``list(dict.values())`` during a concurrent insert raises
    ``RuntimeError: dictionary changed size during iteration``; the Barrier
    makes the collision likely instead of probabilistic.
    """
    from ntrade.domain.instruments.cash import Equity
    ctx = _ctx()
    n_threads = 6
    barrier = threading.Barrier(n_threads)
    errors = []
    stop = threading.Event()

    def writer(tid):
        try:
            barrier.wait(timeout=5)
            i = 0
            while not stop.is_set():
                ctx.register(Equity(f"W{tid}_{i}"))
                i += 1
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            barrier.wait(timeout=5)
            while not stop.is_set():
                ctx.instruments_snapshot()
                ctx.instrument("W0_0")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    # Let the storm run; then stop all threads and join.
    import time
    time.sleep(0.5)
    stop.set()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "thread deadlocked under the context lock"

    assert errors == [], f"Barrier storm raised: {errors}"
