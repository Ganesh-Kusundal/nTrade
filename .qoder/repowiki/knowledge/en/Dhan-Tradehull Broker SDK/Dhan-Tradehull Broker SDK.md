---
kind: external_dependency
name: Dhan-Tradehull Broker SDK
slug: dhan-tradehull
category: external_dependency
category_hints:
    - vendor_identity
    - sdk_real_api
scope:
    - '**'
source_files:
    - ntrade/brokers/dhan.py
    - ntrade/sources/dhan_feed.py
    - ntrade/brokers/dhan_auth.py
---

### Identity
Official Dhan broker Python SDK (imported as `tsl`), providing REST API access for quotes, depth, historical data, option chains, order placement/cancellation/modification, portfolio queries, and WebSocket market data streaming.

### Role in this repo
Primary live broker provider. All market data flows through `DhanMarketFeedSource` (WebSocket) and all order/portfolio operations go through `DhanBroker`, which wraps TradeHull client calls. Auth tokens are persisted via `dhan_auth.py`.

### Integration points
- `ntrade/brokers/dhan.py` — full broker adapter composing auth, mapping, transport, and capability functions
- `ntrade/sources/dhan_feed.py` — WebSocket feed source publishing canonical Tick/Quote events
- `ntrade/brokers/dhan_auth.py` — JWT token file handling (currently writes without restrictive permissions)

### Usage model
- Paper/replay modes use separate providers with identical interfaces
- Capability pattern allows broker-specific features (depth20, kill_switch, margin_calculator) to be registered without touching base classes

### Known constraints
- WebSocket silently disconnects (Indian broker behavior) — `_on_error` is currently a no-op, no auto-reconnect
- Token file written with default permissions (world-readable) — security risk

Verify exact API/params against official TradeHull docs.