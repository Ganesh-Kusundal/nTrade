# Task 8 (T-013): Finalize capability layer — canonical accessors + retire dead facades

**Goal (scoped with user):** Make the production read path use canonical accessors (`instrument.market` / `instrument.market.quote` / `instrument.market.<field>`) instead of reaching into the private `instrument._quote`; add the `Instrument.history` canonical accessor; retire ONLY the two truly-dead facades (`TradeCapability`, `ExtensionCapability`). KEEP `StreamCapability` and `AnalyticsCapability` (real, heavily tested APIs).

## Verified facts
- `Instrument.market` is a `cached_property` returning `MarketCapability` (base.py:123-126). `MarketCapability` exposes accessors: `quote`, `history`, `candles`, `depth`, `ltp`, `bid`, `ask`, `volume`, `oi`, `vwap`, `prev_close`, `spread`, `mid_price`, `is_stale`, `refresh`, `imbalance` (capabilities.py:49-104). NO `high`/`low` accessor → for those use `market.quote.high` / `market.quote.low`.
- Current private `_quote` reads in the plan's PRODUCTION path (route these):
  - `ntrade/scanners/builtin.py`: 31 (`ltp`), 56 (`prev_close`), 96 (`volume`), 148 (`prev_close`), 186 (`high`), 187 (`low`)
  - `ntrade/engines/risk_engine.py`: 101 (`ltp`), 103 (`prev_close`)
  - `ntrade/engines/market_engine.py`: 36 (`bid`, `ask`) — NOTE line 43 `apply_quote(...)` is a WRITE, leave it.
- `ntrade/kernel/trading_session.py` and `ntrade/runner/live_runner.py` have NO direct `_quote`/`_history` reads (verified) — no routing needed there.
- Domain-internal `self._quote` reads in `chain.py`, `expiry.py`, `derivatives.py`, `dhan_mapper.py`, `capabilities.py` (MarketCapability itself), `stream.py`, and broker-internal reads are NOT in scope — leave unchanged.
- `TradeCapability` (capabilities.py:190-206) + its `OrderBuilder` (106-177) + `Instrument.trade` (base.py:128-131): ZERO prod/test callers (grep-verified). Delete.
- `ExtensionCapability` (366-end) + `Instrument.extension` (base.py:148-152): ZERO prod/test callers; only referenced in a docstring. Delete.
- `Instrument._stream`, `StreamCapability`, `AnalyticsCapability` and `Instrument.stream`/`analytics` props all STAY.

## Changes
### 1. `ntrade/domain/instruments/base.py` — add canonical read accessor
Add next to `market` (around line 126):
```python
@property
def history(self) -> "HistoricalSeries":
    """Canonical historical OHLCV accessor (mirrors ``market``)."""
    return self._history
```
Remove the `trade` (128-133) and `extension` (148-152) cached_properties. Update the module docstring (base.py:7) if it references `.extension(`.

### 2. `ntrade/domain/instruments/capabilities.py`
Delete the `OrderBuilder` (106-177) + `TradeCapability` (181-206) classes, and `ExtensionCapability` (366-end). Delete the `.trade`/`.extension` lines from the module docstring (7-11). Keep `MarketCapability`, `StreamCapability`, `AnalyticsCapability`, `DerivativesCapability`, and anything they import/use. Remove now-dangling imports of any deleted symbols (verify by running the suite).

### 3. `ntrade/__init__.py` and `ntrade/domain/instruments/__init__.py`
Remove exports of the deleted facade classes if present (`TradeCapability`, `OrderBuilder`, `ExtensionCapability`). Verify; only edit what actually references them.

### 4. Route production `_quote` reads through `instrument.market`
In `engines/risk_engine.py`, `engines/market_engine.py`, `scanners/builtin.py`, replace `instrument._quote`/`inst._quote` reads with the canonical accessors, preferring a dedicated accessor when it exists (`market.ltp`, `.prev_close`, `.volume`, `.bid`, `.ask`, `.oi`, `.vwap`) and falling back to `market.quote.high` / `market.quote.low` (high/low have no accessor). Do NOT change writes (`apply_quote`). Do NOT touch `_stream`.

### 5. Tests
- `tests/test_instruments.py`: adjust only if it references `.trade`/`.extension` (verify — it uses `.stream`/`.analytics` which stay, so may need no change).
- Grep `tests/` for `TradeCapability`, `OrderBuilder`, `ExtensionCapability`, `.trade`, `.extension(` and update/remove ONLY those references; leave all `\.stream`/`\.analytics`` test calls intact.
- Add (optional) a small assertion that a `TradingSession` paper instrument exposes `.market` and `.history` (canonical access) — only if a natural existing fixture exists; do NOT build a big new fixture.

## Verify
```
./.venv/bin/python -m pytest -q
```
Baseline 631. Report the actual number. If a test references a deleted symbol you missed, update it. Full suite MUST stay green.

### Commit (stage ONLY files you changed; `git status` first — never stage unrelated uncommitted files):
```
git add <changed files under ntrade/instruments, ntrade/engines, ntrade/scanners, ntrade/__init__.py, tests>
git commit -m "T-013 route reads through canonical accessors; retire unused capability facades"
```
Subject EXACTLY: `T-013 route reads through canonical accessors; retire unused capability facades`.

## Report
Write `.superpowers/sdd/briefs/task-8-report.md`: commit hash, `git show --stat`, full suite count, exact set of routed files (the 3 modules), list of removed symbols, and confirmation that `StreamCapability`/`AnalyticsCapability`/`DerivativesCapability`/`MarketCapability` are untouched and all `_stream`/`_analytics` test calls still pass.