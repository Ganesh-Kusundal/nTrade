# Task Group 4 Report — B-009 / HF-001: on_tick broadcasts the tick's own price

## What changed

**Bug:** `MarketEngine.on_tick` published `QuoteUpdatedEvent(ltp=instrument._quote.ltp, ...)` — a read of the instrument's mutable quote *after* `ingest_tick`. `LiveStream.ingest_tick` only mutates `_quote.ltp` for `quote`/`trade`-kind ticks (ntrade/domain/market/stream.py:106-122); for a `depth`-kind tick the broadcast was `0.0`. The broadcast's correctness depended on stream ordering, which is fragile and wrong by construction — the tick event owns the price.

**Fix** (ntrade/engines/market_engine.py:36): broadcast `ltp=event.price` (the tick's own authoritative price). `bid`/`ask` stay as `instrument._quote.bid/ask` (best-effort snapshot).

**Test added** (tests/test_kernel_engines.py, after `test_market_engine_projects_tick`):
`test_market_engine_broadcasts_tick_own_price_for_depth_kind` — publishes `TickEvent(..., price=2498.75, kind="depth")` and asserts the emitted `QuoteUpdatedEvent.ltp == tick.price`. Reuses existing `_kernel()` helper.

TDD flow: wrote the failing test first and confirmed it failed with `ltp=0.0` (broadcast of the unmutated `_quote.ltp`), then applied the one-line fix and watched it pass.

## Test commands and output

Failing test (before impl), from /Users/apple/Downloads/nTrade:
```
$ ./.venv/bin/python -m pytest -q tests/test_kernel_engines.py::test_market_engine_broadcasts_tick_own_price_for_depth_kind -q
F                                                                    [100%]
... AssertionError: assert ([QuoteUpdatedEvent(..., ltp=0.0, ...)] and 0.0 == 2498.75)
```

Full suite (after impl):
```
$ ./.venv/bin/python -m pytest -q
[......................................................................]
622 passed in 5.82s
```

## Commit

- `954c20e` — "B-009 on_tick broadcasts the tick's own price (HF-001)" — only `ntrade/engines/market_engine.py` and `tests/test_kernel_engines.py` staged (explicit `git add` of the two files, no `git add -A`).

## Concerns

None. Pre-existing uncommitted changes to `ntrade/brokers/dhan*.py` and scratch files were left untouched and not committed.
