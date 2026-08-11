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
from ntrade.events.risk import RiskHaltedEvent, RiskResumedEvent
from ntrade.execution.rate_limit import RateLimited
from ntrade.domain.constants import (
    FEED_WATCHDOG_TIMEOUT_S, HEARTBEAT_INTERVAL_S, POLL_INTERVAL_S,
    SYNC_INTERVAL_S, WARMUP_TIMEOUT_S,
)

logger = logging.getLogger("ntrade.runner")


class LiveRunner:
    name = "live-runner"

    def __init__(self, kernel, feed, *, poll_interval: float = POLL_INTERVAL_S,
                 sync_interval: float = SYNC_INTERVAL_S, duration: float | None = None,
                 warmup_timeout: float = WARMUP_TIMEOUT_S, warmup_min_ticks: int = 1,
                 watchdog_timeout: float = FEED_WATCHDOG_TIMEOUT_S, cancel_on_stop: bool = True):
        self.kernel = kernel
        self.feed = feed
        self.poll_interval = float(poll_interval)
        self.sync_interval = float(sync_interval)
        self.duration = duration
        self.warmup_timeout = float(warmup_timeout)
        self.warmup_min_ticks = int(warmup_min_ticks)
        self.watchdog_timeout = float(watchdog_timeout)
        self.cancel_on_stop = bool(cancel_on_stop)
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
        self._last_tick_ts: float | None = None
        self._halt_published = False
        self._heartbeat_interval = HEARTBEAT_INTERVAL_S  # seconds between heartbeats
        self._last_heartbeat = 0.0
        self._timer = time.monotonic
        self._sleep = time.sleep
        self.logger = logger
        self.kernel.bus.subscribe(RiskHaltedEvent, self._on_risk_halted)
        self.kernel.bus.subscribe(RiskResumedEvent, self._on_risk_resumed)
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
        self._last_tick_ts = self._timer()  # watchdog baseline: feed is live now
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
            self._reconcile_orphans()
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
        self._cancel_resting_orders()
        self.feed.stop()
        self.kernel.stop(reason=reason)
        for instrument in self.kernel.ctx.instruments_snapshot():
            broker = getattr(instrument, "broker_adapter", None)
            if broker is not None and hasattr(broker, "stop"):
                broker.stop()
        self.kernel.bus.publish(RunnerStoppedEvent(reason=reason, ts=self.kernel.clock.now()))
        self.started = False

    def _reconcile_orphans(self) -> None:
        """Adopt broker-side orders unknown to the execution tracker (C-4).

        Best-effort on every poll cycle: an ambiguous placement failure may
        have left a real order at the broker. Paper/sim targets have no
        ``reconcile_open`` and are skipped.
        """
        broker_execution = getattr(self.kernel, "broker_execution", None)
        target = broker_execution() if callable(broker_execution) else None
        if target is None or not hasattr(target, "reconcile_open"):
            return
        try:
            adopted = target.reconcile_open()
        except RateLimited:
            return  # quota exhaustion is backoff, not failure — retry next cycle
        except Exception as exc:  # noqa: BLE001 — reconciliation must not kill the loop
            self.logger.warning("reconcile_open failed: %s", exc)
            return
        if adopted:
            self.logger.critical("reconcile_open adopted orphan orders: %s", adopted)

    def _cancel_resting_orders(self) -> None:
        """Cancel every tracked open order before shutdown (H-3).

        Leaving resting LIMIT orders live at the broker after the runner
        stops means fills with no one watching. Opt out with
        ``cancel_on_stop=False`` (logged, deliberate).
        """
        if not self.cancel_on_stop:
            open_ids = self.kernel.open_orders()
            if open_ids:
                self.logger.warning(
                    "stop: leaving %d resting orders live (cancel_on_stop=False): %s",
                    len(open_ids), open_ids,
                )
            return
        failed = []
        for order_id in self.kernel.open_orders():
            try:
                self.kernel.cancel_order(order_id)
            except Exception as exc:  # noqa: BLE001 — shutdown must not crash
                failed.append(order_id)
                self.logger.warning("stop: cancel failed for %s: %s", order_id, exc)
        if failed:
            self.logger.critical(
                "stop: %d resting orders could NOT be cancelled: %s",
                len(failed), failed,
            )

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
        """Halt when the feed delivers no new ticks for ``watchdog_timeout``.

        Wall-clock based (via the injectable ``_timer``): a frozen feed trips
        once after the timeout elapses, not after N fast loop iterations. The
        halt is routed through the RiskEngine (one-shot, rejects all signals
        pipeline-wide) with a latched direct publish as the fallback for
        kernels without a risk engine.
        """
        instruments = self.kernel.ctx.instruments_snapshot()
        total_ticks = sum(
            inst._stream.tick_count
            for inst in instruments
        )
        t = self._timer()
        if self._last_tick_ts is None:
            self._last_tick_ts = t
        if total_ticks > self._last_tick_count:
            self._watchdog_missed = 0
            self._last_tick_ts = t
        else:
            self._watchdog_missed += 1
            stalled = t - self._last_tick_ts
            if stalled >= self.watchdog_timeout:
                self.logger.error(
                    "feed watchdog: no new ticks for %.1fs — halting "
                    "(frozen feed)", stalled,
                )
                self._halt_risk(
                    f"feed watchdog: frozen feed (no new ticks for {stalled:.0f}s)")
        self._last_tick_count = total_ticks

    def _halt_risk(self, reason: str) -> None:
        """Route a halt through the RiskEngine so the whole pipeline stops.

        ``RiskEngine.halt()`` is one-shot (no-op while halted) and publishes
        the RiskHaltedEvent that both rejects new signals and drives the kill
        switch. Falls back to a latched direct publish for kernels without a
        risk engine (the latch resets on RiskResumedEvent).
        """
        engine = getattr(self.kernel, "risk_engine", None)
        if engine is not None and hasattr(engine, "halt"):
            engine.halt(reason)
            return
        if self._halt_published:
            return
        self._halt_published = True
        self.kernel.bus.publish(RiskHaltedEvent(
            reason=reason, ts=self.kernel.clock.now()))

    # ------------------------------------------------------------------ risk
    def _on_heartbeat(self, event) -> None:
        self.logger.info("heartbeat tick_count=%d open_orders=%d",
                         event.tick_count, event.open_orders)

    # Observed feed-drop -> RiskHaltedEvent -> kill switch; independent of the
    # order-timeout consumer below (they never cross-trigger: see
    # tests/test_contract_live_consumers.py)
    def _on_feed_disconnected(self, event) -> None:
        self.logger.warning("feed disconnected: %s — halting", event.reason)
        self._halt_risk(f"feed disconnected: {event.reason}")

    # Order-timeout only cancels the stale order; it must NOT trip the risk halt
    # / kill-switch (see tests/test_contract_live_consumers.py)
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
        # Unified kill: also trip BrokerExecution (cancels open orders + opens
        # the circuit breaker) so the two kill mechanisms are not disconnected.
        broker_exec = getattr(self.kernel, "broker_execution", None)
        if broker_exec is not None:
            target = broker_exec() if callable(broker_exec) else broker_exec
            if target is not None:
                try:
                    target.trip_kill_switch(reason=event.reason)
                except Exception:
                    self.kill_switch_failed = True
                    self.logger.critical(
                        "kill-switch trip_kill_switch failed on "
                        "BrokerExecution — broker may still accept orders",
                    )

    def _on_risk_resumed(self, event: RiskResumedEvent) -> None:
        """Risk engine resumed -> re-arm the broker kill switch (DEACTIVATE).

        Symmetric to _on_risk_halted: a tripped breaker ACTIVATEs the kill
        switch; an explicit resume() DEACTIVATEs it so live orders flow again.
        """
        self.halted = False
        self._halt_published = False  # a new halt may publish again after resume
        if not self.kill_switched:
            return
        ok = True
        for instrument in self.kernel.ctx.instruments_snapshot():
            if instrument.broker_adapter is not None:
                try:
                    instrument.broker.kill_switch(action="DEACTIVATE")
                except Exception:
                    ok = False
                    self.kill_switch_failed = True
                    self.logger.critical(
                        "kill-switch DEACTIVATE failed for %s — "
                        "broker stays halted",
                        instrument.symbol,
                    )
        # Only report re-armed when EVERY broker deactivated — a single failure
        # leaves a halted broker while the runner would otherwise believe
        # trading is re-enabled.
        if ok:
            self.kill_switched = False
            self.logger.info("kill switch DEACTIVATE after risk resume")
