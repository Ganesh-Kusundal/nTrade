"""StrategyRunner — multi-strategy management on top of TradingKernel.

Responsibilities: register several strategies per session, give each its own
risk limits (per-strategy RiskEngine), and report per-strategy status. The
kernel itself stays untouched; the runner only wires strategies and scoped
risk.
"""

from __future__ import annotations

from ntrade.engines.risk_engine import RiskEngine
from ntrade.engines.strategy_engine import Strategy
from ntrade.events.risk import SignalGeneratedEvent


class StrategyRunner:
    """Owns a set of strategies + their per-strategy risk on one kernel.

    Constructing a runner takes over risk screening: the kernel's global
    RiskEngine is paused (a signal must not be approved twice). ``release()``
    restores it. Strategies registered through the runner get a RiskEngine
    scoped to their exact name, so limits are isolated per strategy.
    """

    def __init__(self, kernel):
        self.kernel = kernel
        self.ctx = kernel.ctx
        self._handles: dict[str, Strategy] = {}
        self._risk: dict[str, RiskEngine] = {}
        self._global_risk_paused = False

    # ------------------------------------------------------------------ attach
    def add(self, strategy: Strategy, *, name: str | None = None,
            risk: dict | None = None) -> str:
        """Register a strategy (hot attach); returns its unique name.

        ``risk`` is a dict of RiskEngine kwargs (max_quantity, max_notional,
        max_positions, allowlist) applied to THIS strategy only.
        """
        base = name or getattr(strategy, "name", "strategy")
        unique = self._unique_name(base)
        strategy.name = unique
        self._pause_global_risk()
        self.kernel.register_strategy(strategy)
        self._handles[unique] = strategy
        self._risk[unique] = RiskEngine(self.ctx, strategy=unique, **(risk or {}))
        return unique

    def _unique_name(self, base: str) -> str:
        taken = set(self._handles) | set(self.kernel.strategy_engine.names())
        if base not in taken:
            return base
        i = 2
        while f"{base}#{i}" in taken:
            i += 1
        return f"{base}#{i}"

    def _pause_global_risk(self) -> None:
        # Shared reference count on the kernel: multiple runners may take over
        # global risk simultaneously; the engine is only unsubscribed once and
        # restored only when the last manager releases it.
        count = getattr(self.kernel, "_risk_pause_count", 0)
        if count == 0:
            self.kernel.bus.unsubscribe(
                SignalGeneratedEvent, self.kernel.risk_engine.on_signal
            )
        self.kernel._risk_pause_count = count + 1
        self._global_risk_paused = True

    def running(self, name: str) -> bool:
        strategy = self._handles.get(name)
        return strategy is not None and getattr(strategy, "enabled", True)

    # ------------------------------------------------------------------ queries
    def names(self) -> list[str]:
        return list(self._handles)

    def strategy(self, name: str) -> Strategy | None:
        return self._handles.get(name)

    def risk(self, name: str) -> RiskEngine | None:
        return self._risk.get(name)

    def status(self) -> list[dict]:
        """Per-strategy status: name, enabled, risk limits, approve/reject counts."""
        rows = []
        for name, strategy in self._handles.items():
            engine = self._risk.get(name)
            rows.append({
                "name": name,
                "enabled": getattr(strategy, "enabled", True),
                "max_quantity": engine.max_quantity if engine else None,
                "max_notional": engine.max_notional if engine else None,
                "max_positions": engine.max_positions if engine else None,
                "allowlist": sorted(engine.allowlist) if engine and engine.allowlist else None,
                "approved": engine.approved if engine else 0,
                "rejected": engine.rejected if engine else 0,
            })
        return rows

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> "StrategyRunner":
        """Context-manager ownership: release() runs even if add() raises,
        so the global RiskEngine is never left paused by an abandoned runner
        (D-009 / refcount-leak guard)."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def start(self) -> "StrategyRunner":
        self.kernel.start()
        return self

    def stop(self, reason: str = "") -> "StrategyRunner":
        self.kernel.stop(reason=reason)
        return self

    def release(self) -> None:
        """Restore the kernel's global RiskEngine (undo the takeover)."""
        for engine in list(self._risk.values()):
            self.kernel.bus.unsubscribe(SignalGeneratedEvent, engine.on_signal)
        for strategy in list(self._handles.values()):
            self.kernel.strategy_engine.remove(strategy)
        self._risk.clear()
        self._handles.clear()
        if self._global_risk_paused:
            count = getattr(self.kernel, "_risk_pause_count", 0)
            self.kernel._risk_pause_count = max(0, count - 1)
            if self.kernel._risk_pause_count == 0:
                self.kernel.bus.subscribe(
                    SignalGeneratedEvent, self.kernel.risk_engine.on_signal
                )
            self._global_risk_paused = False
