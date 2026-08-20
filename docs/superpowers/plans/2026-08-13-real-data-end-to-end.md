# Plan: Real-data end-to-end (2026-08-13)

Remove every synthetic/mocked/hardcoded data path so the UI, paper trader and
(optionally) execution run end-to-end on real Dhan market data through the
canonical event pipeline, with truthful liveness/provenance signalling at every
layer.

## Baseline

- HEAD: 7fa01ec. Full pytest 1062-ish baseline; ui tests 130-ish.
- `.env` is gitignored → never `git add .env`; edit the file in place.
- Test fixtures reference "NIFTY AUG FUT"/lot 65 (not "NIFTY OCT FUT"/lot 75);
  implementers extend fixtures as needed.
- `EventStore` has `events()`/`market_events()` (not `load()`); Task 8 uses
  `store.events()`.
- Execution: subagent-driven-development → user chose inline fast execution for
  Tasks 4–8; per-task commits; single review pass at the end.

---

## Task 1: Fail-loud provider selection (DONE — commit 4f16147)

- `api/marketdata.py` `build_service`: no provider configured → raise
  `MarketDataError` (503), never silently default to synthetic.
- `api/server.py` `create_app(provider=...)` passes through.
- Commit: `feat(api): fail-loud provider selection — synthetic is no longer the default`

## Task 2: Real-feed-only pump (DONE — commit 04db2f3)

- `api/live.py` `LiveCandlePump`: remove RNG random-walk fallback; stale
  watchdog; reconnect; IDX quote mode. `_real_feed`/`enabled` gate live.
- Commit: `feat(api): real-feed-only pump — remove RNG walk, add stale watchdog, reconnect, IDX quote mode`

## Task 3: Paper trader canonical pipeline (DONE — commit 8dce903)

- `api/paper_trader.py`: feed real ticks through the canonical kernel pipeline
  (MarketEngine → CandleEngine → strategy → risk → PaperBroker fills); gate on
  live feed.
- Commit: `feat(paper): feed real ticks through the canonical kernel pipeline; gate on live feed`

## Task 4: Honest ticks/candles + persistence (DONE — commit 9186880)

- `api/live.py`: `_record_tick` JSONL recorder; `_persist_bar` parquet live bars.
- `api/marketdata.py`: `_recorded_ticks` reads real ticks; `import json` fix.
- `api/routes.py`: `/api/market/ticks` returns `synthetic: true` + `reason` when
  no real ticks; honest otherwise.
- Commit: `feat(api): persist live bars, record real ticks, honest /ticks (synthetic flag + reason)`

## Task 5: UI liveness/provenance (DONE — commit f6e6857)

- `ui/src/lib/feedStatus.ts`: FeedKind adds `stale`/`synthetic`; `feedKind`
  5-arg `(mode, provider, live, wsStatus, wsStale)`; FEED_META.
- `ui/src/api/client.ts`: export `API_BASE`; MarketSocket watchdog
  (`staleMs=5000`); onStatus extends with `stale`.
- `ui/src/types/market.ts`: WsMessage live_status `'streaming'|'off'|'stale'` +
  `reason?`.
- `ui/src/store/chartStore.ts`: wsStatus adds `'stale'`.
- `ui/src/App.tsx`: DEMO/SYNTHETIC banner when `provider.provider !== 'dhan'`;
  footer feed chip; `TerminalRibbon`.
- `ui/src/components/TerminalRibbon.tsx`: feed chip (meta null-safe).
- `ui/src/hooks/useCandles.ts`: uses `API_BASE`; returns `source`.
- `ui/src/lib/rangeBars.ts`: `calcAutoRange` returns `number|null`;
  `buildRangeBars` returns `[]` on null size.
- `ui/src/lib/valentini.ts`: `calcAutoRange(...) ?? 1.0`.
- `ui/src/lib/marketHours.ts`: `isMcxRoot(root, roots?)`,
  `isMcxSession(exchange, root, roots?)`, `strategySession(exchange, root, roots?)`.
- `ui/src/pages/TradeScreen.tsx`: source chip, `rootList` memo, liveness gate
  (`feed`/`liveIsLive`/`showStaleOverlay`), stale overlay, strategyResult gated
  on `mode==='live' && !liveIsLive`.
- `ui/src/components/StatusBadge.tsx`/`MarketHeader.tsx`: dead code updated to
  new 5-arg signature (`provider` prop).
- Commit (combined with morning VAH/VAL UI work per user choice):
  `feat(ui): truthful feed status (synthetic/stale), provider provenance, live watchdog, honest range bars; morning VAH/VAL UI`

---

## Task 6: Broker-derived data (remove hardcoded values)

**Files:**
- Modify: `api/paper_trader.py:46-62` (`_default_lot_size` → master/contract)
- Modify: `api/server.py:76-77` (initial cash from broker balance when dhan)
- Modify: `api/marketdata.py:61-73` (move `_KNOWN_BASE`/`_base_price` inside `SyntheticProvider` only — they are synthetic-only anchors and must not be reachable by the live/dhan path)
- Modify: `ntrade/brokers/paper.py:35,67-84` (no auto-minted 100.0 quote / synthetic depth)
- Modify: `ntrade/domain/constants.py:86-87` (drop `PAPER_DEFAULT_OPTION_LTP`/`PAPER_HIGH_MARKUP` or require explicit)
- Modify: `tests/test_paper_routes.py`, `tests/test_brokers.py`, `tests/test_paper_broker_state.py`
- Test: `tests/test_paper_routes.py`, `tests/test_brokers.py`

**Interfaces:**
- Consumes: `MarketDataService.master.resolve(symbol).lot_size`; `DhanBroker.get_balance() -> float`; `DhanProvider.get_balance`.
- Produces: `PaperTraderService` sized from the contract's lot size; `initial_cash` = broker balance when dhan; `PaperBroker.get_quote` raises/returns None instead of minting 100.0.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paper_routes.py
def test_paper_lot_size_from_master(tmp_path):
    app = create_app("synthetic", master=_sample_master(tmp_path), live_stream=False)
    app.state.paper._pump._real_feed = True
    app.state.paper._pump.enabled = True
    c = TestClient(app)
    r = c.post("/api/paper/start", json={"symbol": "NIFTY OCT FUT", "exchange": "NFO"})
    assert r.status_code == 200
    assert r.json()["lot_size"] == 75  # contract lot size, not the hardcoded map default
```

```python
# tests/test_paper_broker_state.py
def test_paper_get_quote_never_mints_placeholder():
    from ntrade.brokers.paper import PaperBroker
    b = PaperBroker()
    with pytest.raises(ValueError):
        b.get_quote("NIFTY OCT FUT")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_paper_routes.py tests/test_paper_broker_state.py -q`
Expected: FAIL — lot size comes from the hardcoded map; `get_quote` auto-mints 100.0.

- [ ] **Step 3: Resolve lot size from the instrument master**

`api/paper_trader.py` — replace `_default_lot_size` (46-62) and its use (207):

```python
    def _resolve_lot_size(self, symbol: str, lot_size: int | None) -> int:
        if lot_size and lot_size > 0:
            return int(lot_size)
        contract = self._market.master.resolve(symbol)
        if contract is not None and contract.lot_size:
            return int(contract.lot_size)
        raise HTTPException(
            status_code=422,
            detail=f"unknown lot size for {symbol}; pass lot_size explicitly",
        )
```

Call site (207): `self._lot_size = self._resolve_lot_size(symbol, lot_size)`.

Also move `_base_price`/`_KNOWN_BASE` (`api/marketdata.py:61-73`) so they exist
only as private members of `SyntheticProvider` (module-level `_base_price` was
already removed from `api/live.py` in Task 2; `SyntheticProvider._base_price` at
line 646 delegates to it — inline the `_KNOWN_BASE` dict into `SyntheticProvider`
instead).

- [ ] **Step 4: Broker-derived initial cash**

`api/server.py` (76-77):

```python
    initial_cash = 1_000_000.0
    if live_stream and service.name == "dhan":
        try:
            broker_balance = float(service.provider.get_balance())
            if broker_balance > 0:
                initial_cash = broker_balance
        except Exception:  # noqa: BLE001
            log.warning("broker balance unavailable; keeping paper default")
    app.state.paper = PaperTraderService(service, app.state.pump, initial_cash=initial_cash)
```

`api/server.py` — add `import logging; log = logging.getLogger("api.server")` at top if absent.

- [ ] **Step 5: Stop minting placeholder quotes**

`ntrade/brokers/paper.py:67-78` `get_quote`:

```python
    def get_quote(self, symbol: str):
        """Real quote only. Raises when no quote has been seeded by the feed —
        never fabricates a 100.0 placeholder."""
        quote = self._quotes.get(symbol.strip().upper())
        if quote is None:
            raise ValueError(f"no live quote for {symbol} (paper requires a real feed)")
        return quote
```

Remove the `_seed`-dependent `seed_history` random walk from any read path (keep
a `seed_history` only as an explicit test/backtest helper, documented as
synthetic). Remove synthetic `get_depth` (80-84) or gate it behind an explicit
`synthetic=True` param that raises otherwise.

`ntrade/domain/constants.py:86-87` — delete `PAPER_DEFAULT_OPTION_LTP` and
`PAPER_HIGH_MARKUP`; update the two call sites found by
`grep -rn "PAPER_DEFAULT_OPTION_LTP\|PAPER_HIGH_MARKUP" ntrade` to pass explicit values.

- [ ] **Step 6: Update dependent tests**

`tests/test_brokers.py`, `tests/test_paper_broker_state.py`, `tests/test_paper_broker_reject.py`: replace any reliance on the auto-minted 100.0 quote with an explicit `paper.seed_quote(symbol, price)` call, and update expected lot sizes.

- [ ] **Step 7: Run to verify they pass**

Run: `pytest tests/test_paper_routes.py tests/test_paper_broker_state.py tests/test_paper_broker_reject.py tests/test_brokers.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/paper_trader.py api/server.py api/marketdata.py ntrade/brokers/paper.py ntrade/domain/constants.py tests
git commit -m "feat(api): broker-derived lot size and balance; paper never mints placeholder quotes"
```

---

## Task 7: Data integrity — synthetic isolation, indicator warm-up, order intent

**Files:**
- Modify: `scripts/backfill_parquet.py:137-145` (paper/dry-run writes to `data/ohlcv_synthetic`)
- Modify: `ntrade/engines/indicator_engine.py` (historical warm-up)
- Modify: `ntrade/engines/order_engine.py:27` (order type from strategy intent, not zero/nonzero price)
- Modify: `ntrade/engines/candle_engine.py:49-51` (gate backtest tick-skip to backtest mode)
- Modify: `ntrade/events/risk.py` / `ntrade/engines/strategy_engine.py` / `ntrade/engines/strategies.py` (`SignalGeneratedEvent.order_type`)
- Modify: `tests/test_engine_pipeline.py`, `tests/test_indicators.py`, `tests/test_ema_cross_strategy.py`
- Test: `tests/test_engine_pipeline.py`, `tests/test_indicators.py`

**Interfaces:**
- Consumes: `SignalGeneratedEvent` (`ntrade.events.risk`), `compute_bundle(frame, **params)` (`ntrade.domain.analytics.indicators`).
- Produces: `SignalGeneratedEvent` carries `order_type: "MARKET" | "LIMIT"`; `OrderEngine` uses it; `IndicatorEngine.warm_up(rows)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine_pipeline.py
import types
from datetime import datetime

from ntrade.domain.market_hours import IST
from ntrade.kernel.event_bus import EventBus
from ntrade.events.risk import SignalApprovedEvent, SignalGeneratedEvent

def test_order_engine_uses_strategy_order_intent():
    from ntrade.engines.order_engine import OrderEngine
    captured: list = []

    class StubRouter:
        def submit(self, intent):
            captured.append(intent)
            return None

    bus = EventBus()
    engine = OrderEngine(types.SimpleNamespace(bus=bus), StubRouter())
    ts = datetime.now(tz=IST)

    limit = SignalGeneratedEvent(symbol="NIFTY OCT FUT", exchange="NFO", side="BUY",
                                 quantity=75, price=24300.0, order_type="LIMIT",
                                 metadata={"reference_price": 24300.0}, ts=ts)
    bus.publish(SignalApprovedEvent(signal=limit))
    assert captured and captured[0].order_type == "LIMIT"
    assert captured[0].price == 24300.0

    captured.clear()
    market = SignalGeneratedEvent(symbol="NIFTY OCT FUT", exchange="NFO", side="SELL",
                                  quantity=75, order_type="MARKET",
                                  metadata={"reference_price": 24300.0}, ts=ts)
    bus.publish(SignalApprovedEvent(signal=market))
    assert captured and captured[0].order_type == "MARKET"
    assert captured[0].price == 0.0
```

```python
# tests/test_indicators.py
import types

def test_indicator_engine_warm_up_prevents_cold_start():
    from ntrade.kernel.event_bus import EventBus
    from ntrade.engines.indicator_engine import IndicatorEngine
    ctx = types.SimpleNamespace(bus=EventBus(), instrument=lambda s: None)
    engine = IndicatorEngine(ctx)
    rows = [{"open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0 + i, "volume": 100} for i in range(30)]
    engine.warm_up("NIFTY OCT FUT", rows)
    assert len(engine._rows["NIFTY OCT FUT"]) == 30
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_engine_pipeline.py tests/test_indicators.py -q`
Expected: FAIL — no `order_type` on the event; no `warm_up`.

- [ ] **Step 3: Order type from strategy intent**

`ntrade/events/risk.py` `SignalGeneratedEvent` — add field `order_type: str = "MARKET"`. `ntrade/engines/strategy_engine.py` `Strategy.emit_signal` (45-61) — add `order_type: str = "MARKET"` keyword and pass it into the event:

```python
    def emit_signal(self, *, symbol, exchange: str = Exchange.CASH, side: str,
                    quantity: int, price: float = 0.0, reference_price: float = 0.0,
                    order_type: str = "MARKET", **metadata) -> SignalGeneratedEvent:
        """Emit a trade signal; RiskEngine screens it before it becomes an order.

        ``reference_price`` is the bar-close price that triggered a signal
        (carried through to fills for zero-parity across backtest/replay/
        paper); 0.0 means "use the live LTP". ``order_type`` lets the
        strategy express fill intent (LIMIT at bar close, or MARKET).
        """
        if reference_price:
            metadata["reference_price"] = reference_price
        signal = SignalGeneratedEvent(
            symbol=symbol, exchange=exchange, side=side, quantity=quantity,
            price=price, order_type=order_type, strategy=self.name,
            metadata=metadata, ts=self.ctx.now(),
        )
        self.ctx.bus.publish(signal)
        return signal
```

`ntrade/engines/strategies.py:81,84` — `EmaCrossStrategy` fills at the bar close, so it must emit LIMIT: add `order_type="LIMIT"` to both `emit_signal(...)` calls. MorningVAHVAL (morning_vah_val.py:432,527,550) and Valentini (strategies.py:405,716,819) keep the MARKET default. `ntrade/engines/order_engine.py:24-31`:

```python
        intent = OrderIntentEvent(
            symbol=signal.symbol, exchange=signal.exchange, side=signal.side,
            quantity=signal.quantity,
            order_type=signal.order_type if signal.order_type in ("LIMIT", "MARKET") else "MARKET",
            price=signal.price if signal.order_type == "LIMIT" else 0.0,
            reference_price=signal.metadata.get("reference_price", 0.0),
            strategy=signal.strategy, ts=event.ts,
        )
```

- [ ] **Step 4: Gate the backtest tick-skip**

`ntrade/engines/candle_engine.py:49-51`:

```python
        if self._mode == "backtest" and self._bar_seeded[symbol] == bucket:
            return
```

- [ ] **Step 5: Indicator warm-up + isolation**

`ntrade/engines/indicator_engine.py` — add a warm-up method that seeds the rolling window directly (no events published, so no downstream side effects):

```python
    def warm_up(self, symbol: str, rows: list[dict]) -> None:
        """Seed the rolling OHLCV window from historical bars so live
        decisions aren't delayed by the cold-start warm-up. Rows carry
        open/high/low/close/volume (naive IST wire rows)."""
        buf = self._rows.setdefault(symbol, [])
        for r in rows:
            buf.append({
                "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
            })
        if len(buf) > self._max_rows:
            del buf[:len(buf) - self._max_rows]
```

Call `indicator_engine.warm_up(symbol, history)` from `TradingSession`/`LiveRunner` at session start (pass the last `_MIN_ROWS * 3` rows from `MarketDataService.candles`), guarded by try/except so warm-up failure never blocks live.

`scripts/backfill_parquet.py:137-145` — when `--broker paper`, write to `data/ohlcv_synthetic` (new `--out-dir` default) and print `WARNING: synthetic rows — never mix with data/ohlcv`.

- [ ] **Step 6: Run to verify they pass**

Run: `pytest tests/test_engine_pipeline.py tests/test_indicators.py tests/test_ema_cross_strategy.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ntrade scripts tests
git commit -m "feat(engines): strategy order intent, backtest-only tick guard, indicator warm-up, synthetic backfill isolation"
```

---

## Task 8: Harness/docs truth, EventStore wiring, real bid/ask

**Files:**
- Modify: `api/live.py` (publish real `QuoteEvent` from dhan payload so bid/ask are real)
- Modify: `ntrade/kernel/trading_session.py` / `ntrade/kernel/session.py` (`EventStore` attach option) and `api/server.py` (enable when `NTRADE_EVENT_STORE` set); `api/paper_trader.py` (`__init__` + `_build_session` accept/store the store)
- Modify: `ARCHITECTURE.md:267-278,352,464` + `user-guide/` (remove `live_runner_run.py`, `ema_cross_run.py`, `ResilientKernel`/`ReplayEngine` claims; point to `scripts/live_valentini.py`/`live_smoke.py`)
- Modify: `tests/test_dhan_feed.py`, `tests/test_live_runner.py`, `tests/test_kernel_recording.py`
- Test: `tests/test_kernel_recording.py`, `tests/test_dhan_feed.py`

**Interfaces:**
- Consumes: `dhan_payload_to_events` (`ntrade.sources.dhan_feed`), `EventStore` (`ntrade.storage.event_store`).
- Produces: paper kernel receives real `QuoteEvent` (real bid/ask, no more bid=ask=ltp for quote-kind messages); live kernels can attach `EventStore`; docs no longer cite missing scripts/components.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kernel_recording.py
def test_kernel_with_store_records_events():
    store = EventStore(":memory:")  # use the concrete store's test API
    kernel = TradingKernel(store=store)
    kernel.start()
    kernel.bus.publish(TickEvent(symbol="NIFTY OCT FUT", exchange="NFO", price=24300.0, quantity=25))
    kernel.stop()
    assert len(store.load()) >= 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_kernel_recording.py -q`
Expected: FAIL — the live kernels do not attach a store.

- [ ] **Step 3: Publish real quotes from the dhan payload**

`api/live.py` `_on_dhan_message` (257-263):

```python
    def _on_dhan_message(self, _instance, payload) -> None:
        from ntrade.events.market import QuoteEvent, TickEvent
        from ntrade.sources.dhan_feed import dhan_payload_to_events
        for event in dhan_payload_to_events(payload, self._sec_map, self._clock()):
            if isinstance(event, TickEvent) and event.price > 0:
                self.ingest_tick(event.symbol, event.price, event.quantity)
            elif isinstance(event, QuoteEvent):
                for fn in self._quote_listeners:
                    try:
                        fn(event)
                    except Exception:  # noqa: BLE001
                        log.exception("quote listener failed")
```

Add `self._quote_listeners` and `on_quote(fn) -> unsub` (mirror `on_tick`). `api/paper_trader.py` — in `start()` register `self._pump.on_quote(self._on_pump_quote)` publishing the real `QuoteEvent` into the kernel (so `MarketEngine.on_quote` applies real bid/ask; the framework's bid=ask=ltp approximation in `ntrade/domain/market/stream.py:112` is then superseded by real quote data for quote-kind payloads). Unsubscribe in `stop()`.

- [ ] **Step 4: Wire EventStore**

`ntrade/kernel/trading_session.py` / `session.py` — `TradingSession.live(...)`/`paper(...)` accept `store: EventStore | None = None` and pass it to `TradingKernel(store=store)`. `api/server.py` after pump setup:

```python
    paper_store = None
    if os.environ.get("NTRADE_EVENT_STORE"):
        from ntrade.storage.event_store import EventStore
        paper_store = EventStore("data/events/paper")
    app.state.paper = PaperTraderService(service, app.state.pump,
                                         initial_cash=initial_cash,
                                         store=paper_store)
```

`api/paper_trader.py` — add `store: EventStore | None = None` to `__init__` (stored as `self._store`), and `_build_session` passes `store=self._store` to `TradingSession.paper`.

- [ ] **Step 5: Fix documented-but-missing artifacts**

`ARCHITECTURE.md:352,464` and `user-guide/`: replace every `scripts/live_runner_run.py` / `scripts/ema_cross_run.py` reference with the real harnesses `scripts/live_valentini.py --feed live ...` and `scripts/live_smoke.py`; at `ARCHITECTURE.md:267-278`, annotate `ResilientKernel`/`ReplayEngine` as "not implemented — live reconciliation is handled by `LiveRunner`/`PositionSyncEngine`" (do not delete the design section).

- [ ] **Step 6: Run to verify they pass**

Run: `pytest tests/test_kernel_recording.py tests/test_dhan_feed.py tests/test_live_runner.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/live.py api/paper_trader.py api/server.py ntrade/kernel ntrade/storage ARCHITECTURE.md user-guide tests
git commit -m "feat(api): real bid/ask quotes to paper kernel; EventStore wiring; fix stale harness docs"
```

---

## End-to-End Verification (after Task 8)

```bash
cd /Users/apple/Downloads/nTrade
pytest -q && cd ui && npm run typecheck && npm test && npm run build
```

Then restart and probe the real chain:

```bash
kill 46273 2>/dev/null
nohup ./.venv/bin/python -m api --provider dhan --live-stream --port 8000 > .freebuff/live-api.log 2>&1 &
sleep 2
curl -s http://127.0.0.1:8000/api/market/provider        # {"provider":"dhan","live":true}
curl -s "http://127.0.0.1:8000/api/market/candles?symbol=NIFTY%20OCT%20FUT&exchange=NFO&interval=1m&limit=5" | head -c 400
```

Manual UI check (Vite on 5173): footer reads "live dhan feed", no DEMO banner, feed chip turns `stale` (red, pulsing) within ~5s if the feed dies while the market is open, `source: dhan` chip visible, and `/api/paper/start` works and fills only from real ticks.