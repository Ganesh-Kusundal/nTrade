## Task Group 4 — B-009 / HF-001: on_tick broadcasts the tick's own price

**Finding:** `MarketEngine.on_tick` (ntrade/engines/market_engine.py:25-37) publishes `QuoteUpdatedEvent(ltp=instrument._quote.ltp, ...)` — a read of the instrument's mutable quote after `ingest_tick`, not the tick's authoritative `event.price`. Empirically verified: the broadcast is correct for `trade`/`quote` kinds only because `ingest_tick` mutates `_quote.ltp` first; for a `depth`-kind tick the broadcast is `0.0` (LiveStream.ingest_tick, ntrade/domain/market/stream.py:106-122, only updates `_quote` for `quote`/`trade`). This ordering dependence is fragile and wrong by construction — the tick event owns the price.

**Files:** `ntrade/engines/market_engine.py`, `tests/test_kernel_engines.py`

- [ ] `on_tick`: broadcast `ltp=event.price` (the tick's own price), not `instrument._quote.ltp`. Bid/ask can stay `instrument._quote.bid/ask` (best-effort snapshot).
- [ ] **Tests first, then impl:**
  - [ ] Failing test: publish a `TickEvent(..., kind="depth")` and assert the emitted `QuoteUpdatedEvent.ltp == event.price` (today it is `0.0`).
  - [ ] Existing `test_market_engine_projects_tick` (tests/test_kernel_engines.py:27) still passes — default kind is `trade`, `event.price == 2500.5`.
- [ ] Full suite green: `./.venv/bin/python -m pytest -q`.

---

