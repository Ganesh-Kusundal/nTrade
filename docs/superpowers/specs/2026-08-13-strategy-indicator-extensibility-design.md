# Strategy & Indicator Extensibility — Design

**Date:** 2026-08-13  
**Status:** Draft for review  
**Goal:** Make adding a strategy (paper + chart) and an indicator a small, predictable change — without a plugin framework.

**Chosen path:** Phase A (tiny registries) now → Phase B (server-driven overlays) later, same spec.

---

## Problem

Today, adding one strategy requires shotgun surgery across Python entry points and a full TypeScript state-machine mirror:

| Surface | Actual wiring |
|---------|----------------|
| Paper API | Hard-codes `MorningVAHVAL` (`api/paper_trader.py:197–221`) |
| Live script | Hard-codes `ValentiniScalper` (`scripts/live_valentini.py`) |
| Chart | Hard-codes `runMorningVahVal` (`TradeScreen.tsx:249–260`) |
| Indicators | Fixed list in `compute_bundle`; strategies often self-compute |

There is **no** name→factory map (unlike `ScannerFacade`). Chart overlays reimplement ~1k LOC of Python logic in TS — the parity tax. Paper and chart can silently diverge.

**Success criteria:**
1. New strategy: ≤2 Python files + ≤2 UI files (class + registry line each side).
2. Same strategy id drives paper **and** chart selection.
3. New indicator: 1 Python function + one `compute_bundle` block; optional UI series only if drawn.
4. No plugin discovery, WASM, or mega-config.

---

## Non-goals (YAGNI)

- Auto-discovery / entry points / dynamic imports of strategies
- Strategy-declared indicator dependency graphs
- Shared WASM / codegen Python→TS
- Per-indicator React plugin system
- Replacing `StrategyRunner` or the event bus
- Unifying all scripts into one mega-launcher in Phase A

---

## Phase A — Tiny registries (ship first)

### A1. Python strategy registry

**Create** `ntrade/engines/registry.py` (~30 lines):

```python
STRATEGIES: dict[str, type] = {
    "ema_cross": EmaCrossStrategy,
    "valentini": ValentiniScalper,
    "morning_vah_val": MorningVAHVAL,
}

DEFAULT_STRATEGY = "morning_vah_val"

def build(name: str | None = None, **kwargs) -> Strategy:
    key = (name or DEFAULT_STRATEGY).strip().lower()
    if key not in STRATEGIES:
        raise KeyError(f"unknown strategy {key!r}; known: {sorted(STRATEGIES)}")
    return STRATEGIES[key](**kwargs)

def known() -> list[str]:
    return sorted(STRATEGIES)
```

**Wire one product entry first:** `api/paper_trader.py`

- `POST /api/paper/start` body gains optional `strategy: str` (default `morning_vah_val`).
- `_build_session(..., strategy: str)` calls `build(strategy, symbol=..., exchange=..., lot_size=..., **tuned_kwargs)`.
- Morning-specific `TUNED` / `sl_pad` scaling stays behind an `if name == "morning_vah_val"` branch (or strategy classmethod `default_kwargs`) — do not generalize prematurely.

**Optional (same phase if cheap):** `GET /api/strategies` → `known()` for UI dropdown.

**Live script:** leave `live_valentini.py` as-is in Phase A (explicit Valentini go-live path). Document that new live strategies register in `STRATEGIES` and get a one-line script or env `NTRADE_STRATEGY=` later.

### A2. UI strategy overlay registry

**Create** `ui/src/lib/strategyRegistry.ts`:

```ts
export type StrategyId = 'morning_vah_val' | 'valentini' | 'ema_cross'

export const STRATEGY_OVERLAYS: Record<StrategyId, {
  label: string
  run: (candles: Candle[], opts?: object) => StrategyOverlay
}> = {
  morning_vah_val: { label: 'Morning VAH/VAL', run: runMorningVahVal },
  valentini: { label: 'Valentini', run: (c, o) => /* adapt runValentini → StrategyOverlay */ },
  // ema_cross: chart overlay optional / no-op until needed
}
```

**Change** `TradeScreen.tsx`:
- Persist `strategyId` (same key family as other `ntrade.*` prefs).
- `useMemo` → `STRATEGY_OVERLAYS[strategyId].run(...)` instead of hard-coded `runMorningVahVal`.
- Pass `strategyId` into paper start body so chart and paper agree.

**Change** `PaperTradeControl` / paper client: include `strategy` in start payload.

**Keep** existing TS mirrors (`morningVahVal.ts`, `valentini.ts`) and their tests. Do not rewrite them in Phase A.

### A3. Shared overlay contract (freeze for Phase B)

`ChartPanel.StrategyOverlay` already is:

```ts
{ trades: StrategyTrade[]; levels?: { date, vah, val, poc }[] }
```

**Rule:** every registry overlay returns this shape. Valentini adapter maps `runValentini` trades → `StrategyTrade[]` (levels optional/empty). ChartPanel stays generic — no strategy-specific branches for Phase A beyond existing levels drawing (skip if absent).

### A4. Indicators (unchanged pattern, document it)

To add an indicator:
1. Pure function in `ntrade/domain/analytics/indicators.py`
2. One call + key in `compute_bundle`
3. Test in `tests/test_indicators.py`
4. Optional: series in `ui/src/lib/indicators.ts` + toggle in `ChartPanel` **only if** the chart draws it

Do **not** add an indicator plugin system in Phase A.

### Phase A acceptance

- [ ] `build("morning_vah_val")` and `build("valentini")` construct without error
- [ ] Paper start with `strategy=valentini` registers Valentini (not Morning)
- [ ] Chart strategy selector switches overlay between Morning and Valentini
- [ ] Paper start uses the same id the chart has selected
- [ ] Existing morning/valentini TS tests still pass
- [ ] Adding a fake strategy requires only: class file + one `STRATEGIES` line + one `STRATEGY_OVERLAYS` line (demo in a test or docs example)

---

## Phase B — Server-driven overlays (plan now, build later)

**Goal:** Kill the parity tax for *new* strategies. Chart draws JSON from the server; Python remains the single source of truth for strategy logic.

### B1. Overlay API

```
GET /api/strategy/overlay?strategy=morning_vah_val&symbol=...&exchange=...&interval=1m&start=...&end=...
→ { strategy, trades: [...], levels?: [...], meta?: {...} }
```

- Server loads OHLCV (parquet/Dhan), runs `build(strategy)` in a short-lived paper/backtest kernel **or** a pure “replay for overlay” helper that only collects signals/levels without requiring live broker.
- Response matches `StrategyOverlay` exactly (Phase A contract).

### B2. UI change

- `TradeScreen` for strategies without a TS mirror: fetch overlay from API when candles/window change (debounced).
- Strategies that still have a TS mirror can keep client-side run until migrated.
- Migration order: new strategies → API-only; then Morning/Valentini when stable.

### B3. What Phase B does **not** require

- Streaming tick-by-tick overlay sync (batch on window change is enough)
- Running live Dhan MarketFeed just to draw history (use stored candles)

### Phase B acceptance

- [ ] A strategy with **no** TS file still draws on the chart via API overlay
- [ ] Overlay trades match paper/backtest fills for the same window (spot-check test)
- [ ] Chart works offline for mirrored strategies; API path requires backend

---

## File map

### Phase A (modify/create)

| File | Action |
|------|--------|
| `ntrade/engines/registry.py` | **Create** |
| `api/paper_trader.py` | Wire `build(strategy)` + start body field |
| `api/routes.py` or paper router | Optional `GET /api/strategies` |
| `ui/src/lib/strategyRegistry.ts` | **Create** |
| `ui/src/pages/TradeScreen.tsx` | Selector + registry lookup |
| `ui/src/components/PaperTradeControl.tsx` / `api/client.ts` | Pass strategy id |
| `tests/test_strategy_registry.py` | **Create** (build known/unknown) |
| `ui/src/lib/__tests__/strategyRegistry.test.ts` | **Create** |

### Phase B (later)

| File | Action |
|------|--------|
| `api/` overlay route + service | **Create** |
| `ntrade/` overlay runner helper | **Create** (thin: register strategy, feed candles, collect overlay) |
| `TradeScreen.tsx` | Fetch path for API-only strategies |
| Integration test | Overlay ≡ backtest signals for fixture window |

---

## Risks

| Risk | Mitigation |
|------|------------|
| Valentini TS result shape ≠ `StrategyOverlay` | Adapter in registry; test round-trip keys |
| Morning `TUNED` / `sl_pad` scaling leaks into other strategies | Gate behind strategy id or `classmethod default_paper_kwargs` |
| Paper/chart still diverge under Phase A | Same id + documented parity; Phase B removes dual logic for new work |
| Live money path confusion | Phase A does not change `live_valentini.py`; live stays explicit |

---

## Migration checklist (day-of for a new strategy)

**Phase A:**
1. Subclass `Strategy` in `ntrade/engines/<name>.py`
2. Add to `STRATEGIES` in `registry.py`
3. Add TS overlay runner **or** temporary stub returning `{ trades: [] }`
4. Add to `STRATEGY_OVERLAYS`
5. Select id in UI → paper + chart

**Phase B (preferred for strategy #4+):**
1. Python class + registry line only
2. No TS mirror
3. Chart uses overlay API

---

## Open questions (resolved by this doc)

| Question | Decision |
|----------|----------|
| Execution + chart together? | Yes (user chose both) |
| Kill TS mirrors immediately? | No — Phase A keep; Phase B for new / migrate later |
| Plugin framework? | No |

---

## Approval

Review this file. On approval → `writing-plans` for Phase A implementation tasks only (Phase B stays planned until Phase A lands).
