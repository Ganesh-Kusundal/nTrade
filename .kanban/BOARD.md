# Kanban Board — ntrade
> Digest refreshed 2026-08-02T16:59:58Z (kanban.cli script not on disk — data files updated directly).
> Branch integration-completeness | 177 dirty

## Snapshot
| Metric | Value |
|---|---|
| src files | 88 |
| test files | 64 |
| test cases | 719 passing |
| cards (open) | 0 |
| bugs (open) | 0 |
| risks (open) | 0 |
| stale annotations | 0 |

## Done
| ID | P | Kind | Title | Files | Notes |
|---|---|---|---|---|---|
| K-008 | P2 | debt | M2: BRK- fallback order id merging | ntrade/execution/broker_executor.py |  |
| K-012 | P2 | task | M6: Scanner rate-limiting + snapshot sem | ntrade/scanners/builtin.py |  |
| K-014 | P2 | debt | M8: pandas leak at transport boundary (g | ntrade/brokers/dhan_transport.py |  |
| K-016 | P3 | task | L2: Market vs TradingSession consolidati | ntrade/facade.py, ntrade/kernel/trading_ |  |
| K-018 | P2 | debt | L4: SymbolMaster cache synchronization | ntrade/registry.py |  |
| K-019 | P3 | debt | L5: Greeks zero-filled footgun -> not-co | ntrade/domain/analytics/greeks.py |  |
| K-004 | P1 | debt | H3: Partial-fill delta state must be reb | ntrade/execution/broker_executor.py, ntr |  |
| K-017 | P2 | debt | L3: test_context concurrency test determ | tests/test_context.py |  |
| K-001 | P0 | debt | C1: Broker clock injection — remove now- | ntrade/brokers/dhan.py, ntrade/brokers/p |  |
| K-002 | P0 | bug | C2: Reroute ctx.instruments direct itera | ntrade/runner/live_runner.py, ntrade/sca |  |
| K-003 | P0 | bug | H1: get_ltp/get_quote must raise on tota | ntrade/brokers/dhan_transport.py |  |
| K-005 | P1 | debt | H5: EventStore causal ordering needs mon | ntrade/storage/event_store.py, ntrade/ke |  |
| K-006 | P1 | task | H6: Indian-market cost model (STT, excha | ntrade/execution/costs.py |  |
| K-007 | P1 | task | M1: Stale-feed guard — halt on frozen fe | ntrade/runner/live_runner.py, ntrade/eng |  |
| K-009 | P2 | bug | M3: StrategyRunner refcount leak on aban | ntrade/kernel/runner.py |  |

## Knowledge Graph (graphify)
- 6520 nodes · 11043 edges · 391 communities (built 2026-08-02T10:05:57Z via `graphify update`)
- FRESH — health check OK (no dangling/missing/collapsed edges). Corpus-wide code re-extraction (279 files) grew the graph from 2694 to 6520 nodes, so hub degrees are not comparable to the previous build. 297 changed docs not semantically re-extracted (no GEMINI_API_KEY).

