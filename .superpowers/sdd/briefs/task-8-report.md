# Task 8 (T-013) Report — canonical accessors + retire dead facades

## Commit
- Commit hash (short): `4aedfc7`
- Full: `4aedfc78ccc92b3073118072947afe8bb21ce852`
- Subject: `T-013 route reads through canonical accessors; retire unused capability facades`

## git show --stat

```
 ntrade/domain/instruments/__init__.py     |   8 +-
 ntrade/domain/instruments/base.py         |  22 ++---
 ntrade/domain/instruments/capabilities.py | 135 +-----------------------------
 ntrade/engines/market_engine.py           |   2 +-
 ntrade/engines/risk_engine.py             |   4 +-
 ntrade/scanners/builtin.py                |  12 +--
 tests/test_scanner.py                     |   9 ++
 7 files changed, 28 insertions(+), 164 deletions(-)
```

## Full suite count
`./.venv/bin/python -m pytest -q` → **631 passed** (baseline 631, full suite green).

## Routed files (production `_quote` reads → `instrument.market`)
1. `ntrade/scanners/builtin.py` — `inst.market.ltp()`, `inst.market.prev_close()` (×2), `inst.market.volume()`, `inst.market.quote().high` / `inst.market.quote().low` (no high/low accessor).
2. `ntrade/engines/risk_engine.py` — `instrument.market.ltp()`, `instrument.market.prev_close()`.
3. `ntrade/engines/market_engine.py` — `instrument.market.bid()`, `instrument.market.ask()` (kept `apply_quote` write + `_stream.ingest_tick` untouched).

`ntrade/__init__.py` had no exports of the deleted symbols — no change needed there.

## Removed symbols
- `TradeCapability` (Base / capabilities)
- `OrderBuilder`
- `ExtensionCapability`
- `Instrument.trade` cached_property
- `Instrument.extension` cached_property

## Added
- `Instrument.history` property — canonical historical OHLCV accessor returning `self._history` (placed next to `market`).

## Confirmation
- `StreamCapability`, `AnalyticsCapability`, `DerivativesCapability`, `MarketCapability` — all four kept intact and untouched.
- All `.stream` / `.analytics` test calls still pass (full suite green).
- Bonus: added a lightweight `market` mock to `_make_instrument` in `tests/test_scanner.py` so the scanner tests exercise the routed canonical accessors (no `.stream`/`.analytics` calls modified).