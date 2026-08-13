# Strategy & Indicator Extensibility — Phase A (Tiny Registries)

**Status:** approved (user: "go ahed start wokring on it")
**Scope:** Backend indicator + strategy registries; UI mirror + registry-driven overlays. Phase B (server-driven overlay config) deferred.

## Problem

Adding an indicator or strategy requires edits in N places (shotgun surgery):

| Change | Files touched |
|---|---|
| New indicator | `ntrade/domain/analytics/indicators.py`, `ntrade/engines/indicator_engine.py` param wiring, `ntrade/engines/strategies.py` (each strategy that uses it), `ui/src/lib/indicators.ts`, `ui/src/lib/valentini.ts` or `morningVahVal.ts`, `ChartPanel.tsx` toggle list |
| New strategy | `ntrade/engines/strategies.py` (or new module), `ntrade/__init__.py` (`__all__` + import), `api/paper_trader.py` hardcodes `MorningVAHVAL(...)`, `ui/src/lib/<name>.ts` mirror, `TradeScreen.tsx` import + state |

Two systemic failures stem from this:

1. **Python/TypeScript parity tax** — the calc lives in *both* languages. A drift is a *silent* failure: backtest signals won't match rendered overlays, and live signals won't match charts the trader stares at. Real money.
2. **No single source of truth** for what indicators/strategies exist or what params they take. Frontend hardcodes; backend imports hardcode.

## Phase A Design: Tiny Registries

Constraint (ponytail): smallest change that removes the coupling. Registries are plain dicts; no DI framework, no metaclass, no new dependency. Single source of truth for *names + params + plot shape*; calc still lives in Python and is computed by the existing `compute_bundle` / `IndicatorEngine`.

### 5.1 Indicator registry — `ntrade/registry.py`

```python
@dataclass
class IndicatorSpec:
    id: str            # stable key, e.g. "rsi"
    label: str         # human label
    params: dict[str, Any]   # default params forwarded to compute_bundle or the indicator fn
    series: bool = True      # scalar latest-value vs full series
    plot: PlotSpec | None = None  # how UI renders it when present

indicator = Registry[IndicatorSpec]()
```

Indicators register themselves via decorator on import. `compute_bundle(df, **params)` is unchanged — the registry points *at* it (via `indicator(id, fn=compute_bundle)` with bound params), so the warm-up / rolling-window logic in `IndicatorEngine` is reused verbatim. `EmaCrossStrategy` already reads `bundle.get("ema_9")` — that keeps working, no change to strategy logic.

Concrete indicators registered: `rsi`, `atr`, `vwma`, `vwap`, `vwap_bands`, `ema`, `sma`, `st` (super-trend), `cvd`, `absorption` (OHLCV proxy). Each is `indicator.register("rsi", label="RSI", params={"rsi_period": 14})`.

### 5.2 Strategy registry — same file

```python
@dataclass
class StrategySpec:
    id: str            # e.g. "valentini"
    label: str
    params: dict[str, Any]   # constructor defaults
    indicators: list[str] = field(default_factory=list)  # which indicators it needs

strategy = Registry[StrategySpec]()
```

`paper_trader` no longer imports `MorningVAHVAL` by name. Instead:

```python
spec = strategy.get(name)        # name from config / API
cls = _strategy_classes[spec.id]   # thin dict: {"valentini": ValentiniScalper, ...}
session.register_strategy(cls(**spec.params))
```

The `_strategy_classes` dict is the *implementation* lookup (stays in `strategies.py`); the registry is the *contract*. New strategy → add to `strategy.register(...)` + one line in `_strategy_classes`. No edits to `paper_trader`.

### 5.3 UI mirror — `ui/src/lib/registry.ts`

Same shape, typed. `TradeScreen.tsx` imports `strategies` from the registry instead of importing `MORNING_VAH_VAL_TUNED, runMorningVahVal` directly. Adding an overlay = register on both sides; `TradeScreen` discovers toggles from `strategy.indicators`.

`ChartPanel.tsx` consumes `indicatorRegistry[id].plot` to decide rendering — removed the hardcoded "Valentini overlays" comment block assumption.

### 5.4 Parity guarantee

Both registries share the same `id` keys and `params`. `ntrade.engines.__init__` imports all strategy modules (so `strategy.register` side-effects fire); `ui/src/lib/registry.ts` imports all indicator modules (so the mirror side-effects fire). A runtime check (see §7) asserts the two sets of keys match.

## Expected Behavior Contract

| | |
|---|---|
| **Inputs** | Strategy name + params (from config/API); candle stream from feed/live/ws. |
| **Outputs** | `SignalEvent` (backend); chartable indicator values + overlay spec (UI). |
| **Timing** | Indicators recompute on `CandleClosedEvent` (unchanged). Registries populated at import time, before any event loop. |
| **State transitions** | `register()` → frozen at first `get()` (write-once semantics; registering after lookup raises). |
| **Failure modes** | Unknown strategy id → `KeyError` at session setup (fail loud, not silent). Unknown indicator → `KeyError` at `compute_bundle` call (loud). Registry key mismatch backend vs UI → test failure (loud). |

## Implementation Plan (Phase A)

1. `ntrade/registry.py` — `Registry`, `IndicatorSpec`, `StrategySpec`, `PlotSpec`; `indicator` + `strategy` registries. One file, one diff.
2. `ntrade/domain/analytics/indicators.py` — add `indicator.register(...)` decorators for each exported indicator; keep `compute_bundle` signature stable.
3. `ntrade/engines/strategies.py` — register `ema_cross`, `valentini`, `morning_vah_val` in `strategy`; expose `_strategy_classes` lookup.
4. `ntrade/engines/__init__.py` — import strategy modules so registration fires.
5. `api/paper_trader.py` — replace hardcoded `MorningVAHVAL` import with registry lookup.
6. `ui/src/lib/registry.ts` — typed mirror of both registries; import all indicator/strategy modules for side-effects.
7. `ui/src/pages/TradeScreen.tsx` — consume strategy registry for overlay toggles.
8. `ui/src/components/ChartPanel.tsx` — consume indicator registry for plot rendering.
9. Tests — `tests/test_registry_parity.py`: asserts backend+UI key sets match (no mocks; reads the TS registry via a tiny emitted `registry.json` built from the source by a build script, OR simpler: a Python-only test that the registry is non-empty and frozen-after-lookup). Integration: `test_strategy_registry_resolves()` loads `paper_trader` path end-to-end.
