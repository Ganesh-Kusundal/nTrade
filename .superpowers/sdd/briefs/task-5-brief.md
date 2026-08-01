## Task Group 5 — D-017: gate.py equity derives from the portfolio read model

**Finding:** `gate.py::_equity_trace` (ntrade/runner/gate.py:10-34) re-derives equity from raw `OrderFilledEvent` (cash) + `TickEvent`/`QuoteEvent` (LTP mark) — a third owner of the money state. `RiskEngine.equity` (ntrade/engines/risk_engine.py:44-47) = `account.balance + Σ Position.market_value`, and `Position.market_value = round(quantity * ltp, 2)` (domain/portfolio.py:30-31) where `ltp` is set at fill (portfolio_engine.py:27/46) or broker sync (position_sync.py:56) — never from raw quotes. The gate's LTP mark therefore diverges from the portfolio read model. Fix: the gate must consume the canonical `PositionUpdatedEvent` + `BalanceChangedEvent` stream (published by `PortfolioEngine`, portfolio_engine.py:60-70) so its equity equals `RiskEngine.equity` at every step.

**Files:** `ntrade/runner/gate.py`, `tests/test_paper_gate.py`

- [ ] Rewrite `_equity_trace` to reconstruct from the portfolio read-model events in `kernel.bus.history`:
  - [ ] Start `cash = float(initial_cash)`; keep a `positions: dict[str, Position]`-style map (symbol → quantity/ltp).
  - [ ] On `PositionUpdatedEvent` (events/portfolio.py:13-20: `symbol/quantity/avg_price/ltp`): update the map (quantity 0 → drop).
  - [ ] On `BalanceChangedEvent` (events/portfolio.py:25-27: `balance`): set `cash = event.balance`.
  - [ ] `eq = cash + sum(q * ltp for ...)`; track `peak`, yield `(peak, eq)` — identical semantics to today but on the same MTM basis as `RiskEngine.equity`.
  - [ ] Remove the `TickEvent`/`QuoteEvent` LTP mark and the fill-by-fill cash math. Drop now-unused `QuoteEvent`/`TickEvent` imports from gate.py.
- [ ] `build_paper_report` (gate.py:37+) keeps reading `OrderFilledEvent` for `fills`/`total_charges` — unchanged. `final_equity` already uses `ctx.portfolio` + `account.balance` (gate.py:49-54); leave as-is (it matches `RiskEngine.equity`).
- [ ] **Tests first, then impl:**
  - [ ] Failing test: after a replay run with one BUY fill at `100.0`, `build_paper_report(...)` `max_drawdown_pct` is consistent with the portfolio read model — assert `report["final_equity"]` equals `kernel.risk_engine.equity()` to 2dp.
  - [ ] Existing `test_paper_gate.py` assertions stay green: `fills[0].statutory > 0.0` (default statutory wiring on the sim target), `trade["commission"] == fills[0].commission`, `checklist["total_charges"]` sum, and the empty-run `final_equity == 100_000.0` (no BalanceChangedEvent/PositionUpdatedEvent → trace yields `(100000, 100000)`).
- [ ] Full suite green: `./.venv/bin/python -m pytest -q`.

---

