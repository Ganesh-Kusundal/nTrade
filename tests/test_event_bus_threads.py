"""H1 fix: EventBus serializes publishes (G2-F3)."""
import threading
import time
from datetime import datetime

from ntrade.events.base import Event
from ntrade.kernel.event_bus import EventBus


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
