# 2026-08-02 — Timeframe / Auth / Race Hardening Batch

## Origin
Deep audit of the `3m` timeframe failure chain (docs: user-reported + verified online:
Dhan native API supports only 1/5/15/25/60m + DAY; installed Tradehull 3.3.2 accepts
`['1','2','3','4','5','15','25','60','DAY']` at the library layer but the backend
rejects sub-5m, and `dhan_transport.get_historical` silently returns an empty
`CandleSeries` on any exception). The audit also found auth token-churn (store cleared
on transient probe failure), duplicate feed auth flow, history data race + wall-clock
freshness, and feed stop/reconnect races.

## Cards
| ID | Type | Title | Status |
|---|---|---|---|
| B-013 | bug | Remove 2m/3m/4m from `_DHAN_TIMEFRAMES`; fail fast with correct supported-timeframe error | ✅ done |
| F-001 | feature | Sub-5m timeframes via 1m fetch + resample (`HistoricalSeries.resample`) | ✅ done |
| B-014 | bug | `dhan_transport.get_historical` raises on unexpected exceptions (no silent empty `CandleSeries`) | ✅ done |
| B-015 | bug | Auth: only `_clear_shared` on invalid-token signal; make `refresh_if_needed` atomic under lock | ✅ done |
| D-018 | debt | `DhanMarketFeedSource` reuses broker `tsl` instead of second `get_tradehull` probe chain | ✅ done |
| D-019 | debt | `HistoricalSeries._df` copy-on-write + kernel clock for `is_fresh`/`fetch` (replay parity) | ✅ done |
| D-020 | debt | Serialize `DhanMarketFeedSource` stop/reconnect under a lock | ✅ done |
| B-016 | bug | `DhanTransport.get_balance` must not swallow errors → `0.0` (silent-zero regression; position-sync keeps state on raise) | ✅ done |

## Review findings (2026-08-02, pre-merge review of on-disk work)
1. B-013/F-001/B-014 verified correct: 2m/3m/4m map to `"1"` base, `resample_rule` returns
   pandas rule, transport raises `BrokerDataError` (RateLimited still propagates), DAY
   path intact.
2. **B-016 (new)**: `get_balance` was wrapped in `try/except → return 0.0` — this silently
   zeroes the account on a transient broker error because `PositionSyncEngine._safe_balance`
   relies on the exception to keep current state. Revert to raise (like `get_positions`).
3. B-015 remaining: `refresh_if_needed` must hold a lock across check+refresh (RLock, since
   `authenticate` also takes the lock) to prevent double TOTP mint on concurrent expiry.
4. D-018 remaining: add a test proving the feed reuses an authenticated broker tsl and skips
   the `get_tradehull` probe chain.

## Waves (sequential, exclusive file ownership per wave)
- **Wave 1 (P1 timeframe)** — B-013, F-001, B-014: `dhan_mapper.py`, `dhan_transport.py`, `dhan.py`, `domain/market/history.py` + tests — **done on disk**
- **Wave 2 (P2 auth)** — B-015, B-016, D-018: `dhan_auth.py`, `dhan_auth_provider.py`, `dhan_feed.py`, `runner/feeds.py` + tests — **in progress**
- **Wave 3 (P3 races)** — D-019, D-020: `history.py`, `dhan_feed.py` + tests — **pending**

## Process
- Implement wave → run targeted tests + full suite → code-reviewer in parallel → commit per card
- Mark cards done via kanban CLI after each merge → refresh digest at end
