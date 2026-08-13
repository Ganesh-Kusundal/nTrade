# Plan: Morning VAH/VAL Scalper (Mukul Chowdhury setup) — UI strategy + paper trading

## Goal

Replace the strategy used on the trading UI with the "first-15-minutes FRVP + 10/20 EMA"
morning scalper (Bank Nifty / Nifty futures), show its buy/sell signals on the chart by
default, and allow starting/stopping **paper trading against the selected symbol** on the
backend with ₹1,000,000 paper capital.

## Tuning round (cost-drag fight) — 2026-08-12

Goal: overcome the ~₹1,800/round-trip futures cost drag and get the backtest into
profitable territory.

**What was added** — 4 tuning knobs on `MorningVAHVAL` (all backward-compatible
classic defaults):
- `require_cluster` — only take entries at the 10/20 EMA cluster (drop the 50% tier).
- `reversal_margin_pct` — the reversal close must clear VAL/VAH by this fraction
  (0.001 = 0.1%; filters weak/doji reversals).
- `book_partial` — at T1 either book half + breakeven (classic) or move to breakeven
  and ride the FULL position (half the fills → half the statutory cost).
- `trail_back` — BE-phase trail ratchet lookback (1 = previous bucket, 2-3 = looser,
  lets winners extend).

All mirrored in `ui/src/lib/morningVahVal.ts`. A shared tuned preset lives in
`MorningVAHVAL.TUNED` (Python) / `MORNING_VAH_VAL_TUNED` (TS) and is spread by the
paper trader, the CLI backtest and the UI overlay so every surface runs the same
config. The CLI gained `--no-cluster/--require-cluster/--reversal-margin/--no-partial/
--trail-back` overrides, and a proven-fast path (unsubscribes the kernel's indicator
engine — identical fills/equity at ~550x speed, verified). Sweep harnesses:
`scripts/sweep_morning_vah_val*.py`, `scripts/analyze_sweep2.py`, `scripts/cost_breakdown.py`.

**Sweep results** — 48 (stage-1) + 192 (stage-2) + 72 (micro) configs over the full
54-day window at ~2.5s/run. The tuned preset wins:

| Config | Net | Trades | Win% | MaxDD | 1st/2nd half |
|---|---|---|---|---|---|
| Baseline (classic) | −12.89% | 103 fills | 46.4% | 13.19% | — |
| **Tuned** (rc=1, m=0.001, bp=1, tb=1, pad=30) | **−0.27%** | 6 RT | **66.7%** | **0.60%** | −0.09/−0.18 |
| Tuned − cluster (rc=0) | −1.67% | 11 RT | 41.7% | 1.68% | — |
| Tuned + no partial (bp=0) | −0.14% | 4 RT | 50% | 0.60% | −0.08/−0.06 |

97% of the loss eliminated, drawdown cut ~95%, win rate up ~20pts, gross edge now
POSITIVE (+₹36/trade). **Honest conclusion:** every filter strict enough to clear the
futures STT floor (margin ≥ 0.2%, tight bias) trades ZERO times in 54 days — a 2-min
scalp on index futures is structurally cost-bound (STT ≈ 0.19% of the ~₹1.15M lot
notional ≈ ₹1,000+/round trip). The tuned config is the mathematical optimum on
futures; **the path to real profitability is the video's original instrument —
options (STT on premium, ~10-50× lower)** — blocked locally only by missing options
OHLCV data.

## Chandelier-trail round (exit-side tuning) — 2026-08-12

Goal: a wider ATR-based BE-phase stop (chandelier) lets winners extend toward the day
high and pushes the tuned gross edge further above the STT floor.

**What was added** — `trail_mode` knob on `MorningVAHVAL` (default `"candle"` keeps the
classic per-bucket ratchet; `"chandelier"` = `day_extreme −/+ atr_mult × ATR(2m)`)
plus `atr_mult`/`atr_period` knobs, mirrored in the TS UI strategy (`atrMult`/
`atrPeriod`, with a pandas-`ewm(adjust=True)`-exact ATR helper). CLI gained
`--trail-mode/--atr-mult/--atr-period` overrides. Chandelier unit tests (Python + TS)
prove the divergence: a volatile run-up followed by a calm pullback stops the tight
ratchet while the chandelier holds and keeps ratcheting on new day highs.

**Sweep result — negative (tested, 32 configs + splits).** The chandelier does NOT
beat the candle ratchet:

| Trail | Net | RT | Win% | Avg PnL | MaxDD | Split |
|---|---|---|---|---|---|---|
| candle (tuned baseline) | −0.27% | 6 | 66.7% | +₹36 | 0.60% | −0.09/−0.18 |
| chandelier mult=0.75 per=2 (best) | −0.27% | 6 | 66.7% | +₹36 | 0.60% | −0.09/−0.18 |
| chandelier mult≥1.5 (wider) | −0.29…−0.40% | 6 | 50% | −₹184 | 0.60% | — |

Tight is better: wider chandelier stops turn the few BE-phase winners into 11:00
hard-close give-backs (win rate 66.7% → 50%, avg +₹36 → −₹184) without adding a
single trade. Exit-side tuning is a dead end — the candle ratchet is already the
optimum, and trade count is fixed by the entry filter. The chandelier remains
available as a CLI/constructor option but is NOT the tuned default; the STT floor
conclusion stands: **only options data (STT on premium) changes the math.**

Review fixes (deepseek-flash): `_atr_now` guard now matches pandas `atr`'s own
minimum (`len >= atr_period`, not +1 — TR row 0 is the finite high-low, NaN
terms are skipped by max), the TS ATR helper includes the row-0 TR with
pandas-exact ewm weights, `atr_period` defaults to 3 (2m bars) instead of 14 so
a default chandelier actually ratchets inside the 09:30-11:00 window, and the
TS tuned-parity test now pins the chandelier knob defaults via behavior.
Chandelier validation: 28 Python tests (incl. 2 new chandelier) · 22 vitest ·
typecheck clean.

## Strategy contract (from the source video)

| Rule | Value |
|---|---|
| Timeframe | 2-minute bars (Dhan has no native 2m → derive from the 1m feed, IST-aligned) |
| Execution window | 09:30–11:00 IST (no trades in 09:15–09:30) |
| FRVP | Frozen profile over the first 15 minutes (09:15–09:30 IST) → static VAH/VAL for the morning |
| Prior-day context | UP → longs only · DOWN → shorts only · SIDEWAYS → no trades |
| Long | prior-day UP, price fakes below VAL, bullish reversal closes back above VAL |
| Short | prior-day DOWN, price tests VAH, bearish reversal closes back below VAH |
| Sizing | 100% risk budget when the reversal is at the 10/20 EMA cluster + VAL; else 50% |
| SL | below signal-bar low (long) / above signal-bar high (short) |
| T1 | opposite VA level → book 50%, move SL to breakeven |
| T2 | trail remaining 50% candle-by-candle toward day high/low |
| No-trade filter | no new entries after 11:00; hard-close any open trade at 11:00 |

## Files

Backend
- `ntrade/engines/morning_vah_val.py` (new) — `MorningVAHVAL` strategy (1m closes →
  2m IST-aligned bars, frozen FRVP at 09:30, prior-day bias gate, reversal triggers,
  T1 partial + breakeven + candle trail, 11:00 hard close). Re-exported from
  `ntrade/engines/strategies.py`.
- `tests/test_morning_vah_val.py` — kernel-driven tests (pattern: `tests/test_valentini_strategy.py`).
- `scripts/backtest_morning_vah_val.py` — zero-parity backtest, `initial_cash=1_000_000`,
  sources parquet/dhan/csv; lot size auto-detected from the symbol.
- `api/paper_trader.py` (new) — `PaperTraderService`: start/stop a paper session for a
  symbol (PaperBroker, ₹1M, MorningVAHVAL, per-strategy risk caps), polls the live
  candle pump's in-progress 1m bar and feeds each completed bar into the kernel
  (Quote + CandleClosed); status exposes balance/equity/realized+unrealized PnL/
  positions/fills. Wired into `api/server.py` (`app.state.paper`) with `paper_router`
  (`POST /api/paper/start` · `POST /api/paper/stop` · `GET /api/paper/status`).

UI
- `ui/src/lib/morningVahVal.ts` (new) — pure TS mirror (`runMorningVahVal`) — same logic,
  same 2m aggregation; returns `{ trades, levels, bias }` (frozen per-day VAH/VAL/POC).
- `ui/src/lib/__tests__/morningVahVal.test.ts` — mirror tests (identical synthetic data
  as the Python suite → same levels, val 97.2 / vah 101.04).
- `ui/src/components/ChartPanel.tsx` — `StrategyOverlay` type (shared by both strategies),
  entry/exit markers + amber `½` partial markers + frozen VAH/VAL price lines.
- `ui/src/pages/TradeScreen.tsx` — uses `runMorningVahVal` (buy/sell signals on by
  default) + `PaperTradeControl` for the selected symbol.
- `ui/src/components/PaperTradeControl.tsx` (new) + `ui/src/api/client.ts` paper
  endpoints — start/stop + live status strip (Bal/Eq/PnL/Pos/Fills).

## Validation

- Backend: `tests/test_morning_vah_val.py` 18 passed · `test_valentini_strategy.py`
  24 passed · `test_api_market`/`test_live_ws`/`test_marketdata` 59 passed ·
  `test_paper_trader_service.py` 2 passed · `test_paper_routes.py` 2 passed.
- UI: `npm run typecheck` clean · `npx vitest run` 145 passed (11 files).
- Tuned backtest (tuned preset, fast path): final equity ₹997,286 (−0.27%), 10 fills,
  win rate 66.7%, avg PnL +₹35.6, max DD 0.60%, costs ₹2,927 (6 round trips).
  Half-window splits −0.09/−0.18 confirm the config is not curve-fit.
- Tuning validation: `tests/test_morning_vah_val.py` 22 passed (incl. 4 new knob
  tests) · `test_paper_trader_service.py` 2 passed · `test_paper_routes.py` 2 passed ·
  full UI vitest 149 passed · typecheck clean. Code review (deepseek-flash) fixes
  applied: price-scaled `sl_pad` in the paper trader, trail bounds guard, CLI
  `--no-cluster` override, TS tuned-preset parity test.
- Paper smoke: start/stop via API returns correct state; browser e2e verified the chart
  loads with the new bundle, Paper Trade control starts/stops a ₹1M session on
  BANKNIFTY AUG FUT, and no console errors.
