Task F1: complete (M1 partial-exit avg_price fix, review clean, 351 passing)
Task F2: complete (H2 check() + per-step evaluation, review clean, 353 passing)
Task F3: complete (H1 EventBus RLock, review clean, 355 passing)
Task F4: complete (M2 UTC-pinned candle bucketing, review clean, 357 passing)
Task F5: complete (M3 unverifiable-price rejection, review clean, 358 passing)
Task G1 (C1): complete — broker clock injection (BrokerAdapter.clock/set_clock/_ts, TradingKernel injects, DhanBroker→transport propagation, asof through history/chain expiry), review clean
Task G2 (C2): complete — all ctx.instruments iterators rerouted via instruments_snapshot() + Barrier storm test, review clean
Task G3 (H1): complete — get_ltp raises BrokerDataError on total failure (no silent 0.0), legacy tests updated, review clean
Task G4 (H5): complete — EventStore append-order causal replay (index-as-seq tiebreak), _seq dead state removed, review clean
Task G5 (H6): complete — IndianStatutoryCosts (STT/exchange/SEBI/GST/stamp) incl. method-shadowing fix + intraday-buy STT fix, review clean
Task G6 (M1/M3/M4/M5/M7): complete — stale-feed watchdog halt, StrategyRunner ctx-manager, unregister_all reset, deep snapshot, fail-closed gate scripts, review clean
Task G7 (L1/L3): complete — indicator bare-pass→warn logging, deterministic concurrency test, review clean
Task G8 (K-004 H3): complete — partial-fill delta state rebuildable on recovery (EventStore.open_order_deltas reconstructs per-order filled/remaining from the recorded lifecycle; BrokerExecution.restore_open rehydrates the _open tracker + bumps BRK- seq; ResilientKernel.recover() wires it in; 5 new tests incl. resumed-poll-emits-only-remaining-delta and BRK- id collision), review clean
FINAL: 587 passing, hardening batch + H3 recovery reviewed clean
Task G9 (K-008 M2): complete — BRK- fallback order ids guaranteed unique vs open orders (_next_brk_id loop), no key merging, tests
Task G10 (K-012 M6): complete — scanner rate-limiting (rate_limit_seconds + id-keyed cache in ScannerFacade), tests
Task G11 (K-014 M8): complete — DhanTransport get_positions/get_holdings return domain objects (no pandas leak), tests
Task G12 (K-016 L2): complete — Market facade rewritten as thin adapter over TradingSession (single implementation), tests
Task G13 (K-018 L4): complete — BrokerRegistry class-level RLock guarding shared factory map, tests
Task G14 (K-019 L5): complete — Greeks NOT_COMPUTED sentinel (iv=None) + computed property; implied_volatility returns None on failure, tests
FINAL: 598 passing, all 19 review findings implemented, reviewed clean
Remaining backlog (kanban): K-004 H3 partial-fill delta rebuild, K-008 M2 BRK- id merging, K-012 M6 scanner rate-limit, K-014 M8 pandas leak, K-016 L2 consolidation, K-018 L4 SymbolMaster sync, K-019 L5 greeks footgun
Task G15 (pending items 1-4): complete — committed+pushed H6/H3/M6 (60855ec); FuturesCarryCosts carry/roll accrual in BacktestSimulator + futures_costs_total (feature 2); delivery-equity detection (overnight sells re-price on delivery STT/stamp + buy-leg uplift, delivery_detection opt-out) (feature 3); paper gate report per-fill commission/statutory + checklist total_charges (feature 4). Committed ead8430, 616 passing, 2 review rounds clean
Task Group 1 (B-006/F-004): complete — live broker fills pay commission+statutory, sim parity, review clean (commits 38d5e61..bacd3f6)
Task Group 2 (B-007/F-005): complete — backtest candles carry real OHLCV (bar-authoritative QuoteEvent ingestion, mode-gated), review clean + 1 fix round (commits c215df6..be18e1d)
Task Group 4 (B-009/HF-001): complete — MarketEngine.on_tick broadcasts event.price (depth-kind stale-0.0 fixed), review clean (commit 954c20e)
Task Group 3 (B-008): complete — scanners read canonical keys (rsi_14/stx_10_3/atr_14), compute_bundle emits avg_volume, 3 dead branches eliminated, review clean (commits 42cbbf5, 88572d9)
Task Group 5 (D-017): complete — gate.py equity derives from PositionUpdatedEvent/BalanceChangedEvent, settled at balance events (no pre-fill spike), converges on RiskEngine.equity, review clean + 1 fix round (commits 8cbf69a, 7e38917, 2c2d4e3)
Minor findings recorded (triage at final review): (a) gate docstring says "yields after each state change" but now yields on balance events + initial point — drift; (b) redundant write-then-pop for zero-quantity positions in _equity_trace; (c) pre-existing unrounded eq vs round(q*ltp,2) parity is within 0.01 not bit-exact; (d) initial_cash==0 would ZeroDivisionError (never-used default).
FINAL: 623 passing, all 5 tasks (B-006..B-009, D-017) complete on g4-parity-complexity-batch
Final review: 1 Important cross-task defect fixed (BreakoutScanner crashed on real stx_10_3 direction-string) + gate zero-peak guard + docstring + scanner integration test (commit a3e9f28). 624 passing. Batch ready to merge.
Final review fix round 2: documented VolumeSpikeScanner live volume-unit mismatch (commit df9281a). Batch ready to merge — 13 commits on g4-parity-complexity-batch, 624 passing, pre-existing dhan*.py changes uncommitted.
