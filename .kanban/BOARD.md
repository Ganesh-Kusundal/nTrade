# Kanban Board — ntrade
> Digest refreshed 2026-08-03 (review-findings sweep K-020..K-026 landed; K-027 F-001 label parity added; kanban.cli script not on disk — data files updated directly).
> Branch integration-completeness

## Snapshot
| Metric | Value |
|---|---|
| src files | 88 |
| test files | 53 |
| test functions | 564 |
| cards (open) | 0 |
| bugs (open) | 0 |
| risks (open) | 0 |
| stale annotations | 0 |

## Backlog
| ID | P | Kind | Title | Files | Notes |
|---|---|---|---|---|---|
| _(empty — K-020..K-026 all done)_ |  |  |  |  |  |

## Done
| ID | P | Kind | Title | Files | Notes |
|---|---|---|---|---|---|
| K-008 | P2 | debt | M2: BRK- fallback order id merging | ntrade/execution/broker_executor.py |  |
| K-012 | P2 | task | M6: Scanner rate-limiting + snapshot sem | ntrade/scanners/builtin.py |  |
| K-014 | P2 | debt | M8: pandas leak at transport boundary (g | ntrade/brokers/dhan_transport.py |  |
| K-016 | P3 | task | L2: Market vs TradingSession consolidati | ntrade/facade.py, ntrade/kernel/trading_ |  |
| K-018 | P2 | debt | L4: SymbolMaster cache synchronization | ntrade/registry.py |  |
| K-019 | P3 | debt | L5: Greeks zero-filled footgun -> not-co | ntrade/domain/analytics/greeks.py |  |
| K-020 | P1 | bug | get_executed_price/_and_time swallow Rat | ntrade/brokers/dhan.py |  |
| K-021 | P2 | debt | Broker-level data reads still swallow fa | ntrade/brokers/dhan.py |  |
| K-022 | P2 | task | Gap/Imbalance scanners unthrottled (rate | ntrade/scanners/builtin.py |  |
| K-023 | P2 | debt | VolumeSpikeScanner live volume-unit mism | ntrade/scanners/builtin.py |  |
| K-024 | P2 | debt | OrderFacade defaults TradeType.MIS for a | ntrade/domain/orders/order.py |  |
| K-025 | P2 | debt | HistoricalSeries.resample label/closed c | ntrade/domain/market/history.py, ntrade/ |  |
| K-026 | P3 | chore | gate._equity_trace write-then-pop redund | ntrade/runner/gate.py |  |
| K-027 | P2 | debt | F-001 resample_history labels right-edge | ntrade/brokers/dhan_mapper.py, tests/test |  |
| K-004 | P1 | debt | H3: Partial-fill delta state must be reb | ntrade/execution/broker_executor.py, ntr |  |
| K-017 | P2 | debt | L3: test_context concurrency test determ | tests/test_context.py |  |
| K-001 | P0 | debt | C1: Broker clock injection — remove now- | ntrade/brokers/dhan.py, ntrade/brokers/p |  |
| K-002 | P0 | bug | C2: Reroute ctx.instruments direct itera | ntrade/runner/live_runner.py, ntrade/sca |  |
| K-003 | P0 | bug | H1: get_ltp/get_quote must raise on tota | ntrade/brokers/dhan_transport.py |  |
| K-005 | P1 | debt | H5: EventStore causal ordering needs mon | ntrade/storage/event_store.py, ntrade/ke |  |
| K-006 | P1 | task | H6: Indian-market cost model (STT, excha | ntrade/execution/costs.py |  |
| K-007 | P1 | task | M1: Stale-feed guard — halt on frozen fe | ntrade/runner/live_runner.py, ntrade/eng |  |
| K-009 | P2 | bug | M3: StrategyRunner refcount leak on aban | ntrade/kernel/runner.py |  |

## Known Bugs (open)
- _(none — K-020 closed by the review-findings sweep)_

## Knowledge Graph (graphify)
- 2160 nodes · 5034 edges · 104 communities (built 2026-07-31T19:32:56Z)
- STALE (60 modified, 24 deleted, 26 new) — run `/graphify update`

