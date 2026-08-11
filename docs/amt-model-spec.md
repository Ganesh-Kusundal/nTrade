# Fabio Valentini Auction Market Theory — Literal Model Spec

> Authoritative extraction of Fabio Valentini's actual execution model, distinct
> from the "Fabio-inspired" approximations circulating online. This spec is the
> contract the nTrade `ValentiniScalper` implements; every condition below is
> stated as an unambiguous algorithm. Data-feasibility notes mark each step as
> achievable on NSE/Dhan data (1m OHLCV + live L2 depth) or as requiring true
> order-flow/tape data that Dhan does not provide.

## 0. Model statement

The model is **not** an indicator strategy. It is an
**Auction Market Theory + Volume Profile + Order Flow** execution model with a
single decision equation:

```
TRADE = DIRECTION  AND  LOCATION  AND  AGGRESSION
```

If any of the three is missing → NO TRADE. The execution pipeline is:

```
1. DIRECTION → 2. LOCATION → 3. AGGRESSION → 4. EXECUTE → 5. TRAIL
```

There are two distinct behaviors — a **continuation model** (primary) and a
**reversal model** (secondary, profit-gated). They must not be mixed
indiscriminately.

### Not the model

The following are approximations, not Fabio's model:

- RSI / MACD / moving-average crossovers / Bollinger Bands
- OHLCV candle-delta (close-vs-open) as "CVD"
- A bare volume spike
- ATR breakouts
- `POC + VAH + VAL + volume > average + bullish candle = BUY`

The real model requires the relationship between
**auction state → location → actual aggression → price response**.

---

## 1. DIRECTION — who is controlling the auction

Established **first**, from price structure + volume. It is a narrative about
the current leg, not a per-bar indicator.

**Bullish** (buyers control the auction):
- Buyers are aggressive.
- Price accepts higher levels.
- Previous highs / structure are broken.
- Volume supports the upward move.

**Bearish** (sellers control the auction): the mirror image.

The analyst does **not** start with "price is at support, therefore buy."
They start with "**who is actually controlling the auction?**"

### 1.1 Data-feasibility

- Structure (HH/HL vs LH/LL on swing pivots) — **achievable** from OHLCV.
- Volume confirming the direction — **achievable** from OHLCV.
- Auction bias (VWAP / AVWAP) — **achievable** from OHLCV.
- True aggression (bid/ask absorption) — **not achievable** historically;
  partially via live L2 depth.

---

## 2. LOCATION — wait for price to reach a meaningful area

Direction alone = no trade. Location alone = no trade. **Both** are required.

Once direction is established, **do not chase price**. Wait for a retracement
into a meaningful **Point of Interest (POI)**:

- Previous structure
- Volume Profile levels: **LVN / HVN / POC**
- VWAP / AVWAP
- Important support/resistance
- Previous balance area
- Other auction-derived levels

### 2.1 Data-feasibility

- Volume Profile (POC/VAH/VAL) — **achievable** from OHLCV (leg-anchored).
- VWAP / AVWAP — **achievable** from OHLCV.
- LVN / HVN detection — **achievable** from OHLCV (low/high-volume nodes in the
  profile) but not yet implemented in nTrade.

---

## 3. AGGRESSION — the actual trigger

This is the step most often lost in simplified implementations.

Suppose direction = LONG and price pulls back into the POI. You **do not
immediately buy**. Wait for order flow to show the winning side becoming
aggressive again:

```
Price reaches POI
  → order-flow aggression appears
  → price responds
  → momentum / follow-through appears
  → LONG
```

The trigger is **not** `high volume = buy`. It is:

```
Volume + price response = aggression
```

Fabio uses order flow as the execution trigger — aggression, momentum,
follow-through — rather than entering blindly with a limit order.

### 3.1 Data-feasibility

- Absorption bars (big volume, tiny range) at the value edge — **achievable**
  as an OHLCV proxy.
- True trade-by-trade aggression — **not achievable** (no tape on Dhan).
  nTrade labels its OHLCV CVD as a proxy; live L2 depth adds a partial signal.

---

## 4. EXECUTE — tight invalidation

Entry is aggressive (market), not a resting limit. The stop is tight and
placed around the **invalidation point** — the structure / aggressive
order-flow area. The stop belongs where the aggression thesis is *wrong*, not
at an arbitrary volatility offset.

Because the invalidation is tight, Fabio can operate with relatively small risk
and many trades.

### 4.1 Data-feasibility

- Market entry — **achievable**.
- Stop at structure / aggression invalidation — **achievable** from OHLCV
  (e.g. one step outside the leg's value edge, or just beyond the last pivot).

---

## 5. TRAIL — follow the auction

No `entry → fixed 2R target`. Once the trade starts working, follow the
auction:

- **If** volume continues increasing **and** price continues following →
  continue holding / trailing.
- **If** the structure breaks **or** the volume-price relationship changes →
  get out.

Fabio follows the market with the stop as volume and price continue confirming
the position.

### 5.1 Data-feasibility

- Structure-break exit (close beyond the trailed pivot) — **achievable**.
- Volume-price divergence exit (new high on lower volume) — **achievable** from
  OHLCV.
- True auction-following from the tape — **not achievable**; the OHLCV proxy
  is the practical ceiling.

---

## 6. REVERSAL model (secondary)

Not the primary setup. Fabio is more willing to take reversals **after already
having profit for the day**.

```
Strong directional move
  → market becomes overextended
  → auction moves away from equilibrium
  → Volume Profile identifies fair value
  → POC = equilibrium
  → reversal opportunity
  → target = POC
```

The POC is treated as the volume-based fair-value / equilibrium level.

### 6.1 Behaviors, kept separate

| Behavior | Sequence |
|---|---|
| **Trend continuation** | Imbalance → retracement → POI → aggression → continuation |
| **Mean reversion** | Excess / failed auction → reversal → equilibrium / POC |

The reversal model must **not** be mixed indiscriminately with the directional
model.

### 6.2 Data-feasibility

- Overextension detection (beyond VWAP band / K×step beyond leg POC) —
  **achievable**.
- Absorption at the extreme — **achievable** (OHLCV proxy).
- Response back toward POC — **achievable**.
- Profit-for-the-day gate — **achievable** (track strategy realized PnL).

---

## 7. Required data summary

| Data | Required by | Available on NSE/Dhan |
|---|---|---|
| 1m OHLCV + volume | Direction, Location, Aggression (proxy), Execute, Trail, Reversal | ✅ Yes |
| VWAP / AVWAP | Direction, Location | ✅ Yes |
| Swing-pivot structure | Direction, Trail, Execute | ✅ Yes |
| Volume Profile (POC/VAH/VAL/LVN/HVN) | Location, Reversal | ✅ Yes (POC/VAH/VAL; LVN/HVN pending) |
| Bid/ask absorption (L2 depth) | Aggression | ⚠️ Live only |
| Trade tape / footprint delta | Aggression (true) | ❌ No |

---

## 8. nTrade gap register (against this spec)

| Gap | Spec step | Status |
|---|---|---|
| No up-front Direction gate | 1 | **To fix** |
| Fixed R-multiple TP instead of auction-following trail | 5 | **To fix** |
| No reversal model | 6 | **To fix** (PnL-gated fade to POC) |
| LVN/HVN not detected | 2 | Deferred (uses POC/VAH/VAL as POIs) |
| OHLCV CVD is a proxy for true aggression | 3 | Documented limitation (no tape) |
| Live L2 depth only tightens live entries | 3 | Documented parity asymmetry (opt-in) |