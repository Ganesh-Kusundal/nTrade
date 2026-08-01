---
kind: external_dependency
name: Dhan Trading Platform SDK
slug: dhan-tradehull
category: external_dependency
category_hints:
    - vendor_identity
    - sdk_real_api
    - auth_protocol
scope:
    - '**'
---

### Identity & Role

### Integration Points
- `ntrade/brokers/dhan.py` - Main broker adapter wrapping Tradehull functionality
- `ntrade/brokers/dhan_auth.py` - Authentication and token management layer
- `ntrade/sources/dhan_feed.py` - Market data feed source using Tradehull's websocket

### Authentication Protocol
Three auth modes supported:
1. **Access Token** (`mode="access_token"`) - Daily expiration, requires regeneration
2. **API Key** (`mode="api_key"`) - Browser-based login flow
3. **PIN + TOTP** (`mode="pin_totp"`) - Lifetime PIN, preferred for automation

### Critical Constraints
- APP tokens have hard 24-hour lifetime and cannot be renewed (`DH-905` error)
- Access tokens expire daily and must be regenerated before market open
- TOTP rate-limited to 1 refresh per 2 minutes
- MARKET orders banned for F&O since April 2026 SEBI regulation

### Usage Pattern
Tradehull instance created once per session with credentials, then used for all API calls. Token lifecycle managed through shared store with fallback mechanisms when tokens expire.

### Verify exact API/params against official docs