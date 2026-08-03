The module is a flat collection of domain types grouped by data shape:
- `quote.py` — frozen dataclasses `Quote` and `Tick` representing point-in-time values.
- `depth.py` — frozen `DepthLevel` and `MarketDepth` snapshots of the order book.
- `candles.py` — `CandleSeries`, a thin typed wrapper around a pandas DataFrame with an escape hatch via `to_dataframe()` and `__getattr__` delegation.
- `history.py` — `HistoricalSeries`, owns fetch lifecycle, caching, resampling, and indicator computation through the instrument's broker adapter (`instrument.broker_adapter.get_historical`).
- `stream.py` — `LiveStream` and `SubscriptionState` manage per-instrument subscription lifecycle, a bounded deque of ticks, and an event bus (`on_*` handlers) that updates the instrument's `_quote` on ingest.

Dependency direction is one-way: `stream.py` imports `Quote`/`Tick`; `history.py` imports from `ntrade.domain.instruments.base` only under `TYPE_CHECKING`; no cross-imports between the other files. The broker boundary is enforced by returning domain-typed wrappers instead of raw DataFrames, keeping pandas behind the adapter layer.