"""ResilientKernel — a TradingKernel with crash recovery.

After a crash, a fresh kernel replays the recorded EventStore's causal stream
(market events + fills) to rebuild instrument, candle, indicator and portfolio
state deterministically before resuming live trading. The same event stream
drives the same engines as live/replay/backtest — the zero-parity invariant.
"""

from __future__ import annotations

from ntrade.events.base import Event
from ntrade.kernel.clock import ReplayClock, TradingClock
from ntrade.kernel.session import TradingKernel
from ntrade.storage.event_store import EventStore


class ResilientKernel(TradingKernel):
    """A TradingKernel whose state can be rebuilt from a recorded EventStore.

    ``store`` is the crash-recovery source (read-only during ``recover()``);
    pass ``record`` to also append the recovered session's events to a fresh
    EventStore for continued auditing. ``recover()`` must run before any
    strategies are registered — replaying the causal stream with strategies
    attached would re-emit signals and re-trade.
    """

    def __init__(
        self,
        store: EventStore | None = None,
        *,
        mode: str = "replay",
        clock: TradingClock | None = None,
        record: EventStore | None = None,
        **kw,
    ):
        self.recovery_store = store
        super().__init__(mode=mode, clock=clock or ReplayClock(), store=record, **kw)
        self.recovered_events = 0
        self.recovered_at = None
        self._recovered = False

    # ------------------------------------------------------------------ recovery
    def recover(self) -> "ResilientKernel":
        """Rebuild state from the store's causal stream without re-trading.

        Replays ``store.recovery_events()`` (market data + fills) through the
        engine stack. Derived events (signals, intents, candle/indicator
        updates) are recomputed by the kernel, never re-fed — so nothing
        double-applies and no new orders are generated during recovery.
        """
        if self._recovered:
            raise RuntimeError("recover() already ran — recovery is one-shot")
        if self.recovery_store is None:
            raise RuntimeError("ResilientKernel has no recovery store to replay")
        if self.strategy_engine.strategies:
            raise RuntimeError(
                "recover() must run before registering strategies — "
                "replaying with strategies attached would re-trade"
            )
        events = self.recovery_store.recovery_events()
        if not events:
            self._recovered = True
            return self
        # Pause recording while we replay the causal stream, so the recovered
        # events are not re-appended to the audit log.
        if self.store is not None:
            self.bus.unsubscribe(Event, self._record)
        try:
            self.run_replay(events)
        finally:
            if self.store is not None:
                self.bus.subscribe(Event, self._record)
        self._reseed_execution()
        self.recovered_events = len(events)
        self.recovered_at = self.clock.now()
        self._recovered = True
        return self

    def _reseed_execution(self) -> None:
        """Continue order numbering past the recovered fills.

        Replaying fill events does not run the simulator, so its sequence
        counter is untouched and the first live order would collide with a
        recovered ``SIM-…`` id. Bump any simulated target past the max id.
        """
        if not self.recovery_store:
            return
        from ntrade.events.order import OrderFilledEvent
        from ntrade.execution.simulator import SimulatedExecution

        max_seq = 0
        for event in self.recovery_store.recovery_events():
            if not isinstance(event, OrderFilledEvent):
                continue
            for token in str(event.order_id).split("-")[-1:]:
                if token.isdigit() and int(token) > max_seq:
                    max_seq = int(token)
        if max_seq == 0:
            return
        for target in self.router._targets.values():
            if isinstance(target, SimulatedExecution):
                target._seq = max_seq

    # ------------------------------------------------------------------ helpers
    def last_event_ts(self):
        """Timestamp of the last causal event (resume point), or None.

        Uses the causal stream (market + fills), not the full record: derived
        events like an orderly-shutdown candle flush would otherwise point the
        resume cursor past the last market event actually consumed.
        """
        if self.recovery_store is None:
            return None
        events = self.recovery_store.recovery_events()
        return events[-1].ts if events else None

    def snapshot(self) -> dict:
        """Recovered state summary: balance, positions, instruments."""
        instruments = self.ctx.instruments_snapshot()
        return {
            "mode": self.mode,
            "recovered_events": self.recovered_events,
            "recovered_at": self.recovered_at,
            "balance": self.ctx.account.balance,
            "positions": [p.as_dict() for p in self.ctx.portfolio.positions],
            "instruments": {i.symbol: i.snapshot() for i in instruments},
        }
