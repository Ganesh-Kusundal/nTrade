"""LiveRunner — the orchestration loop that drives the live trading day.

Wraps a TradingKernel + a MarketFeedSource and closes the three audit gaps that
pure event plumbing cannot: keep the feed flowing, periodically reconcile the
order book with the broker (poll_orders/sync_positions), and trip the broker
kill switch when the risk engine halts. Everything else is just events.
"""

from __future__ import annotations

import logging
import time
import signal

from ntrade.events.lifecycle import (HeartbeatEvent, FeedDisconnectedEvent,
                                     RunnerStartedEvent, RunnerStoppedEvent)
from ntrade.events.risk import RiskHaltedEvent

logger = logging.getLogger("ntrade.runner")


class LiveRunner:
    name = "live-runner"

    def __init__(self, kernel, feed, *, poll_interval: float = 5.0,
                 sync_interval: float = 60.0, duration: float | None = None,
                 warmup_timeout: float = 15.0, warmup_min_ticks: int = 1):
        self.kernel = kernel
        self.feed = feed
        self.poll_interval = float(poll_interval)
        self.sync_interval = float(sync_interval)
        self.duration = duration
        self.warmup_timeout = float(warmup_timeout)
        self.warmup_min_ticks = int(warmup_min_ticks)
        self.polls = 0
        self.syncs = 0
        self.started = False
        self.halted = False
        self.kill_switched = False
        self.kill_switch_failed = False
        self._last_poll = 0.0
        self._last_sync = 0.0
        self._last_tick_count = 0
        self._watchdog_missed = 0
        self._watchdog_max_missed = 3
        self._heartbeat_interval = 30.0  # seconds between heartbeats
        self._last_heartbeat = 0.0
        self._timer = time.monotonic
        self._sleep = time.sleep
        self.logger = logger
        self.kernel.bus.subscribe(RiskHaltedEvent, self._on_risk_halted)
        from ntrade.events.order import OrderFilledEvent, OrderTimeoutEvent
        self.kernel.bus.subscribe(OrderFilledEvent, self._on_fill)
        self.kernel.bus.subscribe(HeartbeatEvent, self._on_heartbeat)
        self.kernel.bus.subscribe(FeedDisconnectedEvent, self._on_feed_disconnected)
        self.kernel.bus.subscribe(OrderTimeoutEvent, self._on_order_timeout)

    # ------------------------------------------------------------------ loop
    def start(self) -> "LiveRunner":
        if self.started:
            return self
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

    def step(self) -> None:
        """One loop iteration: poll/sync when their intervals are due."""
        if not self.started:
            raise RuntimeError("LiveRunner.start() must be called before step()")
        self._evaluate_risk()
        if self.halted:
            return
        t = self._timer()
        if t - self._last_poll >= self.poll_interval:
            emitted = self.kernel.poll_orders()
            if emitted:
                self.logger.info("poll_orders: %d lifecycle events", len(emitted))
            self.polls += 1
            self._last_poll = t
        if t - self._last_sync >= self.sync_interval:
            self.kernel.sync_positions()
            self.syncs += 1
            self._last_sync = t
        self._check_feed_watchdog()
        self._emit_heartbeat_if_due()

    def _evaluate_risk(self) -> None:
        """Check the risk circuit breakers every loop tick — without this they
        are only evaluated when a strategy emits a signal. Halting is handled by
        the existing RiskHaltedEvent -> _on_risk_halted wiring."""
        engine = getattr(self.kernel, "risk_engine", None)
        if engine is None or not hasattr(engine, "check"):
            return
        engine.check()

    def run(self, duration: float | None = None) -> None:
        """Drive the loop until `duration` seconds have elapsed (or forever)."""
        if not self.started:
            self.start()
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _handle_signal(signum, frame):
            self.stop(reason=f"signal {signum}")

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
        try:
            target = duration if duration is not None else self.duration
            start_t = self._timer()
            while self.started:
                self.step()
                if target is None:
                    self._sleep(0.1)
                    continue
                elapsed = self._timer() - start_t
                if elapsed >= target:
                    break
                self._sleep(min(target - elapsed, 0.1))
            if self.started:
                self.stop()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)

    def stop(self, reason: str = "") -> None:
        if not self.started:
            return
        self.feed.stop()
        self.kernel.stop(reason=reason)
        for instrument in self.kernel.ctx.instruments_snapshot():
            broker = getattr(instrument, "broker_adapter", None)
            if broker is not None and hasattr(broker, "stop"):
                broker.stop()
        self.kernel.bus.publish(RunnerStoppedEvent(reason=reason, ts=self.kernel.clock.now()))
        self.started = False

    def _emit_heartbeat_if_due(self) -> None:
        t = self._timer()
        if t - self._last_heartbeat >= self._heartbeat_interval:
            instruments = self.kernel.ctx.instruments_snapshot()
            self.kernel.bus.publish(HeartbeatEvent(
                tick_count=sum(inst._stream.tick_count for inst in instruments),
                open_orders=len(self.kernel.open_orders()),
                ts=self.kernel.clock.now(),
            ))
            self._last_heartbeat = t

    def _check_feed_watchdog(self) -> None:
        instruments = self.kernel.ctx.instruments_snapshot()
        total_ticks = sum(
            inst._stream.tick_count
            for inst in instruments
        )
        if total_ticks > self._last_tick_count:
            self._watchdog_missed = 0
        else:
            self._watchdog_missed += 1
            if self._watchdog_missed >= self._watchdog_max_missed:
                self.logger.error(
                    "feed watchdog: no new ticks for %d checks — "
                    "publishing RiskHaltedEvent (frozen feed)",
                    self._watchdog_missed,
                )
                self.kernel.bus.publish(RiskHaltedEvent(
                    reason="feed watchdog: frozen feed (no new ticks)",
                    ts=self.kernel.clock.now(),
                ))
        self._last_tick_count = total_ticks

    # ------------------------------------------------------------------ risk
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

    def _on_fill(self, event) -> None:
        self.logger.info("FILL %s %s x%d @ %.2f", event.side, event.symbol,
                         event.quantity, event.fill_price)

    def _on_risk_halted(self, event: RiskHaltedEvent) -> None:
        """Risk circuit breaker tripped -> emergency broker kill switch."""
        self.halted = True
        for instrument in self.kernel.ctx.instruments_snapshot():
            if instrument.broker_adapter is not None:
                try:
                    instrument.broker.kill_switch(action="ACTIVATE")
                    self.kill_switched = True
                except Exception:
                    self.kill_switch_failed = True
                    self.logger.critical(
                        "kill-switch ACTIVATE failed for %s — "
                        "broker may still accept orders",
                        instrument.symbol,
                    )
