# Production Deployment Examples

<cite>
**Referenced Files in This Document**
- [pyproject.toml](file://pyproject.toml)
- [.gitignore](file://.gitignore)
- [ARCHITECTURE.md](file://ARCHITECTURE.md)
- [deployment.md](file://.agents/skills/dhan-tradehull/references/deployment.md)
- [live_runner.py](file://ntrade/runner/live_runner.py)
- [resilient.py](file://ntrade/kernel/resilient.py)
- [event_store.py](file://ntrade/storage/event_store.py)
- [dhan_auth.py](file://ntrade/brokers/dhan_auth.py)
- [dhan_auth_provider.py](file://ntrade/brokers/dhan_auth_provider.py)
- [benchmark_latency.py](file://scripts/benchmark_latency.py)
- [live_runner_run.py](file://scripts/live_runner_run.py)
- [live_read_check.py](file://scripts/live_read_check.py)
- [pre_deploy_check.py](file://scripts/pre_deploy_check.py)
- [rate_limit.py](file://ntrade/execution/rate_limit.py)
</cite>

## Update Summary
**Changes Made**
- Added comprehensive section on Pre-Deployment Validation with Quota Headroom Check
- Updated Live Read-Only Check section to include quota status reporting
- Enhanced Pre-Deploy Gate section with quota validation workflow
- Added BrokerRateGate integration details for production deployments
- Updated troubleshooting guide with quota-related issues

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#setailed-component-analysis)
6. [Pre-Deployment Validation](#pre-deployment-validation)
7. [Dependency Analysis](#dependency-analysis)
8. [Performance Considerations](#performance-considerations)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Conclusion](#conclusion)
11. [Appendices](#appendices)

## Introduction
This document provides production deployment examples and operational guidance for nTrade applications. It covers containerization, environment configuration, monitoring setup, latency benchmarking, performance optimization, error handling, logging, alerting, health checks, graceful shutdown, crash recovery, scaling considerations, resource management, memory optimization for high-frequency trading, dashboards/metrics collection, profiling, security best practices, secret management, and compliance considerations. The guidance is grounded in the repository's architecture and runtime components to ensure practical, code-aligned operations.

**Updated** Added comprehensive pre-deployment validation with quota headroom checking to prevent deployments when broker API rate limits are exhausted.

## Project Structure
nTrade follows a layered, event-centric architecture with clear separation between domain, execution, broker adapters, and infrastructure. For production, you will orchestrate:
- A TradingKernel (live or replay mode)
- A MarketFeedSource (synthetic or live Dhan feed)
- A LiveRunner loop that polls orders, syncs positions, monitors feed health, and triggers risk kill switches
- An EventStore for audit and crash recovery
- Authentication providers for secure broker access
- **New**: Pre-deployment validation system with quota headroom checking

```mermaid
graph TB
subgraph "Runtime"
LR["LiveRunner"]
K["TradingKernel"]
FEED["MarketFeedSource<br/>Synthetic/Dhan"]
STORE["EventStore"]
AUTH["Dhan Auth Provider"]
RATE["BrokerRateGate"]
end
LR --> K
LR --> FEED
K --> STORE
K --> AUTH
FEED --> K
RATE --> AUTH
```

**Diagram sources**
- [live_runner.py:1-196](file://ntrade/runner/live_runner.py#L1-L196)
- [resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)
- [event_store.py:1-236](file://ntrade/storage/event_store.py#L1-L236)
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)
- [rate_limit.py:76-174](file://ntrade/execution/rate_limit.py#L76-L174)

**Section sources**
- [ARCHITECTURE.md:1-389](file://ARCHITECTURE.md#L1-L389)
- [pyproject.toml:1-25](file://pyproject.toml#L1-L25)

## Core Components
- LiveRunner: Orchestrates kernel lifecycle, feed warmup, periodic polling/sync, heartbeat, watchdog, and risk-triggered kill switch activation.
- ResilientKernel: Extends TradingKernel with deterministic crash recovery from EventStore causal stream without re-trading.
- EventStore: Append-only JSONL record of events; supports market-only replay, open-order delta reconstruction, and recovery event ordering.
- Dhan Auth Provider: Manages token lifecycle, shared store usage, proactive refresh, and PIN+TOTP fallback.
- **New**: BrokerRateGate: Multi-window, multi-class broker API rate limiting with quota headroom monitoring.

Key operational behaviors:
- Feed warmup ensures connectivity before starting strategies.
- Heartbeat and watchdog detect frozen feeds and halt safely.
- Risk engine halts trigger broker kill switches across all instruments.
- Crash recovery rebuilds state deterministically and resumes safely.
- **New**: Pre-deployment validation checks quota headroom before go-live.

**Section sources**
- [live_runner.py:1-196](file://ntrade/runner/live_runner.py#L1-L196)
- [resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)
- [event_store.py:1-236](file://ntrade/storage/event_store.py#L1-L236)
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)
- [rate_limit.py:76-174](file://ntrade/execution/rate_limit.py#L76-L174)

## Architecture Overview
The production runtime composes the kernel, feed, runner, and storage into a resilient pipeline. The kernel processes canonical events through engines (market, candle, indicator, strategy, risk, order, portfolio). Execution targets route intents to simulated or broker execution paths.

```mermaid
sequenceDiagram
participant Ops as "Operator / Systemd"
participant Runner as "LiveRunner"
participant Kernel as "TradingKernel"
participant Feed as "MarketFeedSource"
participant Store as "EventStore"
participant Broker as "BrokerAdapter"
participant RateGate as "BrokerRateGate"
Ops->>Runner : start()
Runner->>Kernel : attach + start()
Runner->>Feed : start()
Runner->>Feed : wait_ready(timeout, min_ticks)
alt Warmup OK
Runner-->>Kernel : publish RunnerStartedEvent
loop Poll/Sync
Runner->>Kernel : poll_orders()
Runner->>Kernel : sync_positions()
Runner->>Runner : _check_feed_watchdog()
Runner->>Kernel : publish HeartbeatEvent
end
else Warmup Failed
Runner->>Kernel : stop(reason="feed warmup failed")
Runner-->>Kernel : publish RunnerStoppedEvent
end
Note over Runner,Broker : On RiskHaltedEvent -> activate kill_switch on all instruments
Note over RateGate,Broker : Rate limit enforcement with quota headroom monitoring
```

**Diagram sources**
- [live_runner.py:55-141](file://ntrade/runner/live_runner.py#L55-L141)
- [live_runner_run.py:26-64](file://scripts/live_runner_run.py#L26-L64)
- [rate_limit.py:76-174](file://ntrade/execution/rate_limit.py#L76-L174)

**Section sources**
- [ARCHITECTURE.md:156-301](file://ARCHITECTURE.md#L156-L301)

## Detailed Component Analysis

### LiveRunner Orchestration
LiveRunner drives the live day by:
- Starting kernel and feed, performing feed warmup
- Periodically polling orders and syncing positions
- Monitoring feed liveness via watchdog
- Emitting heartbeats for observability
- Activating broker kill switches upon risk halts
- Handling SIGTERM/SIGINT for graceful shutdown

```mermaid
flowchart TD
Start(["start()"]) --> Attach["attach(kernel) + kernel.start()"]
Attach --> FeedStart["feed.start()"]
FeedStart --> Warmup{"wait_ready(timeout,min_ticks)?"}
Warmup --> |No| StopFailed["kernel.stop(reason='feed warmup failed')<br/>publish RunnerStoppedEvent"]
Warmup --> |Yes| Started["started=True<br/>publish RunnerStartedEvent"]
Started --> Loop["run(duration)"]
Loop --> Step["step(): evaluate_risk()<br/>poll_orders()<br/>sync_positions()<br/>watchdog()<br/>heartbeat()"]
Step --> SignalCheck{"SIGTERM/SIGINT?"}
SignalCheck --> |Yes| GracefulStop["stop(reason='signal ...')"]
SignalCheck --> |No| Loop
GracefulStop --> End(["stopped"])
```

**Diagram sources**
- [live_runner.py:55-141](file://ntrade/runner/live_runner.py#L55-L141)

**Section sources**
- [live_runner.py:1-196](file://ntrade/runner/live_runner.py#L1-L196)

### ResilientKernel Crash Recovery
ResilientKernel enables deterministic recovery after crashes:
- Replays only causal events (market data + fills)
- Recomputes derived events (signals, candles, indicators)
- Rebuilds open-order deltas to resume partial fills correctly
- Reseeds execution sequences to avoid ID collisions
- Provides snapshot and last_event_ts for verification

```mermaid
classDiagram
class ResilientKernel {
-recovery_store : EventStore
-recovered_events : int
-recovered_at : datetime
-_recovered : bool
+recover() ResilientKernel
+snapshot() dict
+last_event_ts() datetime?
-_reseed_execution() void
-_rebuild_open_orders() void
}
class TradingKernel {
+mode : str
+clock : TradingClock
+store : EventStore?
+bus : EventBus
+router : ExecutionRouter
+ctx : Context
}
class EventStore {
+recovery_events() list[Event]
+open_order_deltas() dict
+append(event) EventStore
+events(...) list[Event]
}
ResilientKernel --|> TradingKernel : "extends"
ResilientKernel --> EventStore : "reads causal stream"
```

**Diagram sources**
- [resilient.py:17-147](file://ntrade/kernel/resilient.py#L17-L147)
- [event_store.py:183-211](file://ntrade/storage/event_store.py#L183-L211)

**Section sources**
- [resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)
- [event_store.py:1-236](file://ntrade/storage/event_store.py#L1-L236)

### EventStore Audit and Recovery
EventStore provides:
- Append-only JSONL persistence
- Causal event ordering using append index for tiebreaking
- Market-only replay for deterministic recomputation
- Open-order delta reconstruction for partial fill continuity
- Safe decoding that skips unknown event types

```mermaid
flowchart TD
A["append(event)"] --> B["encode to JSON-safe dict"]
B --> C["write line to file (flush)"]
C --> D["in-memory list append"]
E["recovery_events()"] --> F["filter market + fills"]
F --> G["sort by ts then append index"]
H["open_order_deltas()"] --> I["walk accepted/filled/updated/rejected"]
I --> J["return remaining open deltas"]
```

**Diagram sources**
- [event_store.py:89-104](file://ntrade/storage/event_store.py#L89-L104)
- [event_store.py:183-211](file://ntrade/storage/event_store.py#L183-L211)
- [event_store.py:137-181](file://ntrade/storage/event_store.py#L137-L181)

**Section sources**
- [event_store.py:1-236](file://ntrade/storage/event_store.py#L1-L236)

### Dhan Authentication and Token Management
Authentication flow:
- Load .env credentials
- Try shared token store if present and not near expiry
- Use access_token if valid within buffer
- Fall back to PIN+TOTP when needed
- Proactive background refresh scheduled post-auth

```mermaid
sequenceDiagram
participant App as "Application"
participant Provider as "DhanAuthProvider"
participant Auth as "dhan_auth.get_tradehull"
participant Store as "Shared Token Store"
participant Env as ".env"
App->>Provider : authenticate()
Provider->>Auth : get_tradehull(env, env_path)
Auth->>Env : load_dotenv()
Auth->>Store : read token if exists
alt Shared token valid
Auth-->>Provider : Tradehull instance
else Access token valid
Auth-->>Provider : Tradehull instance
else TOTP fallback
Auth-->>Provider : Tradehull instance (PIN+TOTP)
end
Provider->>Provider : schedule_proactive_refresh()
Provider-->>App : connected Tradehull
```

**Diagram sources**
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)
- [dhan_auth.py:114-151](file://ntrade/brokers/dhan_auth.py#L114-L151)

**Section sources**
- [dhan_auth.py:42-118](file://ntrade/brokers/dhan_auth.py#L42-L118)
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)

### Latency Benchmarking and Performance Profiling
Use the provided benchmark script to measure kernel tick throughput and write results to a JSON file. This helps establish baseline performance and track regressions.

```mermaid
flowchart TD
CLI["scripts/benchmark_latency.py"] --> Args["parse args (--ticks, --out)"]
Args --> Kernel["create TradingKernel(mode='replay', ReplayClock())"]
Kernel --> Bench["measure_tick_throughput(kernel, n_ticks)"]
Bench --> Stats["stats = {ticks, wall_seconds,<br/>events_per_sec, ticks_per_sec, fanout}"]
Stats --> Write["write JSON to .benchmarks/latency.json"]
Write --> Print["print stats"]
```

**Diagram sources**
- [benchmark_latency.py:18-32](file://scripts/benchmark_latency.py#L18-L32)

**Section sources**
- [benchmark_latency.py:1-37](file://scripts/benchmark_latency.py#L1-L37)

### Live Runner Harness Usage
The harness supports synthetic mode (offline rehearsal) and live mode (real Dhan websocket). It wires kernel, feed source, and runner with configurable intervals and durations.

```mermaid
sequenceDiagram
participant User as "User"
participant Script as "live_runner_run.py"
participant Kernel as "TradingKernel"
participant Source as "build_source()"
participant Runner as "LiveRunner"
User->>Script : run with args (feed, symbol, days, duration, poll, sync)
Script->>Kernel : create with clock (Live/Replay)
alt feed == synth
Script->>Kernel : register Index(symbol)
Script->>Kernel : fetch historical frame
end
Script->>Source : build_source(kernel, feed, symbol, exchange, frame, live_kwargs)
Script->>Runner : LiveRunner(kernel, source, poll_interval, sync_interval)
Runner->>Runner : run(duration)
Runner-->>Script : session summary (ticks, polls, syncs, fills, balance)
```

**Diagram sources**
- [live_runner_run.py:26-64](file://scripts/live_runner_run.py#L26-L64)

**Section sources**
- [live_runner_run.py:1-69](file://scripts/live_runner_run.py#L1-L69)

## Pre-Deployment Validation

### Quota Headroom Check Implementation
The pre-deployment validation system includes comprehensive quota headroom checking to prevent deployments when broker API rate limits are exhausted. This critical safety feature ensures that trading systems don't deploy into environments where they cannot make necessary API calls.

```mermaid
flowchart TD
Start(["Pre-Deploy Check"]) --> TokenCheck["Token Freshness Check"]
TokenCheck --> PaperGate["Paper Gate Validation"]
PaperGate --> LiveRead["Live Read-Only Check"]
LiveRead --> QuotaCheck["Quota Headroom Check"]
QuotaCheck --> LiveSmoke["Live Smoke Test"]
LiveSmoke --> Result{"All Checks Pass?"}
Result --> |Yes| Deploy["Proceed with Deployment"]
Result --> |No| Fail["Fail Deployment"]
```

**Diagram sources**
- [pre_deploy_check.py:75-107](file://scripts/pre_deploy_check.py#L75-L107)
- [live_read_check.py:90-199](file://scripts/live_read_check.py#L90-L199)
- [rate_limit.py:133-157](file://ntrade/execution/rate_limit.py#L133-L157)

### Quota Status Reporting
The quota headroom check provides detailed visibility into broker API rate limiting across different quota classes:

- **Quote Quota**: 1 request per second
- **Data Quota**: 5 requests per second, 100,000 per day
- **Order Quota**: 10 requests per second, 250 per minute, 1000 per hour, 7000 per day
- **Non-Trading Quota**: 20 requests per second

The system reports the tightest window (shortest span) per quota class to identify the most restrictive rate limit currently in effect.

```mermaid
flowchart TD
QuotaSnap["BrokerRateGate.status()"] --> Windows["Analyze All Windows"]
Windows --> Tightest["Find Tightest Window<br/>(Shortest Span)"]
Tightest --> Report["Generate Status Row:<br/>quote=used/limit;data=used/limit;<br/>order=used/limit;non_trading=used/limit"]
Report --> Blocked{"Any Quota Blocked?"}
Blocked --> |Yes| DEGRADED["Status: DEGRADED<br/>Deployment Should Fail"]
Blocked --> |No| PASS["Status: PASS<br/>Deployment Can Proceed"]
```

**Diagram sources**
- [live_read_check.py:19-47](file://scripts/live_read_check.py#L19-L47)
- [rate_limit.py:133-157](file://ntrade/execution/rate_limit.py#L133-L157)

### Pre-Deploy Gate Workflow
The unified pre-deploy gate orchestrates multiple validation stages in sequence:

1. **Token Freshness Check**: Validates JWT token expiry and warns about near-expiry tokens
2. **Paper Gate Validation**: Runs paper trading simulation with exit criteria
3. **Live Read-Only Check**: Executes comprehensive read-only endpoint validation including quota headroom
4. **Live Smoke Test**: Performs basic framework functionality validation

```mermaid
sequenceDiagram
participant Operator as "Operator"
participant PreDeploy as "pre_deploy_check.py"
participant Token as "Token Validator"
participant Paper as "paper_gate_run.py"
participant LiveRead as "live_read_check.py"
participant Smoke as "live_smoke.py"
Operator->>PreDeploy : Execute pre-deploy check
PreDeploy->>Token : Validate token freshness
alt Token Near Expiry
Token-->>PreDeploy : FAIL - Refresh Required
PreDeploy-->>Operator : Exit 1 (FAIL)
else Token Valid
Token-->>PreDeploy : OK
PreDeploy->>Paper : Run paper gate validation
Paper-->>PreDeploy : Paper gate result
PreDeploy->>LiveRead : Run live read check (with --strict)
LiveRead-->>PreDeploy : Read check result + quota status
PreDeploy->>Smoke : Run smoke test
Smoke-->>PreDeploy : Smoke test result
PreDeploy-->>Operator : Final pass/fail decision
end
```

**Diagram sources**
- [pre_deploy_check.py:75-107](file://scripts/pre_deploy_check.py#L75-L107)
- [live_read_check.py:90-199](file://scripts/live_read_check.py#L90-L199)

### Quota Headroom Decision Logic
The deployment decision logic considers both FAIL and DEGRADED statuses:

- **FAIL Status**: Always fails deployment (crashed endpoints are never go-live safe)
- **DEGRADED Status**: Fails deployment in strict mode (recommended for go-live), tolerates in diagnostic mode
- **Quota Exhaustion**: When any quota window is at capacity, the system reports DEGRADED status and blocks deployment

```mermaid
flowchart TD
Results["Validation Results"] --> Analyze["Analyze Status Codes"]
Analyze --> Failed{"Any FAIL Status?"}
Failed --> |Yes| Block["Block Deployment<br/>Exit Code: 1"]
Failed --> |No| Degraded{"Any DEGRADED Status?"}
Degraded --> |Yes| Strict{"Strict Mode?"}
Strict --> |Yes| Block
Strict --> |No| Allow["Allow Deployment<br/>Exit Code: 0"]
Degraded --> |No| Allow
```

**Diagram sources**
- [live_read_check.py:50-65](file://scripts/live_read_check.py#L50-L65)
- [pre_deploy_check.py:87-107](file://scripts/pre_deploy_check.py#L87-L107)

**Section sources**
- [live_read_check.py:1-247](file://scripts/live_read_check.py#L1-L247)
- [pre_deploy_check.py:1-112](file://scripts/pre_deploy_check.py#L1-L112)
- [rate_limit.py:76-174](file://ntrade/execution/rate_limit.py#L76-L174)

## Dependency Analysis
Production dependencies include Python packages defined in the project manifest and runtime integrations with Dhan-Tradehull. Secrets are excluded from version control via .gitignore.

```mermaid
graph TB
Pkg["pyproject.toml"] --> Deps["pandas, numpy, python-dotenv,<br/>Dhan-Tradehull"]
Git[".gitignore"] --> Secrets[".env, Dependencies/, caches,<br/>.freebuff/, .pytest_cache/, .benchmarks/"]
RateLimit["rate_limit.py"] --> Quotas["Quota Classes:<br/>QUOTE, DATA, ORDER, NON_TRADING"]
LiveRead["live_read_check.py"] --> RateGate["BrokerRateGate Integration"]
```

**Diagram sources**
- [pyproject.toml:1-25](file://pyproject.toml#L1-L25)
- [.gitignore:1-25](file://.gitignore#L1-L25)
- [rate_limit.py:31-47](file://ntrade/execution/rate_limit.py#L31-L47)
- [live_read_check.py:107-110](file://scripts/live_read_check.py#L107-L110)

**Section sources**
- [pyproject.toml:1-25](file://pyproject.toml#L1-L25)
- [.gitignore:1-25](file://.gitignore#L1-L25)

## Performance Considerations
- CPU-bound scanning and event processing: limit concurrent strategies per core budget.
- Memory growth: monitor EventBus history size; consider bounded buffers for long-running sessions.
- Feed throughput: use synthetic mode to rehearse pipelines offline; validate warmup thresholds.
- Order polling intervals: tune poll_interval and sync_interval to balance freshness vs overhead.
- Event fanout: measure events_per_sec and fanout to identify heavy subscribers or expensive handlers.
- Resource isolation: run each strategy in its own process/container to contain failures and resource spikes.
- **New**: Rate limit awareness: monitor quota headroom to prevent API throttling during peak trading hours.
- **New**: Pre-deployment validation: always run quota headroom checks before deploying to production.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Feed warmup failure: verify connectivity and minimum ticks; inspect logs for connection errors.
- Frozen feed: watchdog detects no new ticks; risk halt triggers automatically; check network and broker status.
- Kill switch failure: ensure broker adapter supports kill_switch capability; log critical failures and verify broker-side enforcement.
- OOM in live sessions: cap EventBus history length; offload heavy analytics to async workers.
- Token expiry: use PIN+TOTP auth to avoid daily restarts; monitor JWT expiry and proactive refresh scheduling.
- Port conflicts: kill by port rather than process name; confirm PID ownership after restart.
- **New**: Quota exhaustion: check rate limit status and reduce API call frequency; implement exponential backoff.
- **New**: Pre-deployment failures: review quota headroom report and adjust trading strategy to stay within limits.

Operational tips:
- Use systemd for auto-restart and boot persistence.
- Follow logs with journalctl or tail -f app.log.
- Validate new deployments by running synthetic rehearsals first.
- **New**: Always execute pre-deploy checks with --strict flag in production environments.
- **New**: Monitor quota headroom trends to anticipate rate limit issues before they occur.

**Section sources**
- [deployment.md:51-118](file://.agents/skills/dhan-tradehull/references/deployment.md#L51-L118)
- [live_runner.py:154-174](file://ntrade/runner/live_runner.py#L154-L174)
- [dhan_auth_provider.py:58-74](file://ntrade/brokers/dhan_auth_provider.py#L58-L74)
- [live_read_check.py:50-65](file://scripts/live_read_check.py#L50-L65)

## Conclusion
nTrade's event-centric design, resilient kernel, and robust authentication provide a solid foundation for production deployments. By leveraging the LiveRunner orchestration, EventStore-based audit and recovery, structured benchmarking tooling, and comprehensive pre-deployment validation with quota headroom checking, teams can deploy scalable, observable, and safe trading systems. The new quota validation system ensures that deployments only proceed when broker API rate limits allow sufficient headroom for trading operations. Adhering to the operational guidance here ensures reliable live trading, efficient resource usage, strong security posture, and protection against rate limit violations.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Containerization Example
- Base image: Python 3.10+ with system dependencies for pandas/numpy
- Install package from pyproject.toml
- Mount secrets via environment variables or mounted volumes (.env excluded from repo)
- Run scripts/live_runner_run.py in synth mode for rehearsal; switch to live mode with proper credentials
- Use health checks to probe heartbeat events or a lightweight HTTP endpoint if exposed
- **New**: Include pre-deploy check execution in container startup for automated validation

[No sources needed since this section provides general guidance]

### Environment Configuration
- Required environment variables: DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN (optional), DHAN_PIN, DHAN_TOTP_SECRET, DHAN_TOKEN_PATH, DHAN_COOLDOWN_PATH
- Use .env for local development; inject secrets at runtime in production
- Validate token expiry and proactive refresh behavior
- **New**: Configure rate limit windows if customizing BrokerRateGate behavior

**Section sources**
- [dhan_auth.py:114-151](file://ntrade/brokers/dhan_auth.py#L114-L151)

### Monitoring and Alerting
- HeartbeatEvent: emit periodically with tick counts and open orders
- Watchdog: trigger RiskHaltedEvent on frozen feed
- Metrics: collect events_per_sec, ticks_per_sec, fanout from benchmarks
- Dashboards: visualize latency.json trends, heartbeat metrics, and risk events
- Alerting: on RiskHaltedEvent, feed watchdog trips, or kill switch failures
- **New**: Monitor quota headroom metrics and alert on approaching rate limits
- **New**: Track pre-deployment validation success rates and quota exhaustion events

**Section sources**
- [live_runner.py:143-174](file://ntrade/runner/live_runner.py#L143-L174)
- [benchmark_latency.py:18-32](file://scripts/benchmark_latency.py#L18-L32)
- [live_read_check.py:19-47](file://scripts/live_read_check.py#L19-L47)

### Security Best Practices
- Never commit secrets; enforce .gitignore rules
- Use PIN+TOTP auth for long-running services
- Restrict broker API access to whitelisted IPs
- Limit process privileges and isolate containers
- Rotate tokens proactively and monitor expiry
- **New**: Validate quota headroom before deployment to prevent API abuse
- **New**: Monitor rate limit violations as potential security indicators

**Section sources**
- [.gitignore:1-25](file://.gitignore#L1-L25)
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)

### Compliance Considerations
- Maintain audit trails via EventStore JSONL
- Ensure deterministic replay for reproducibility and debugging
- Enforce risk limits and circuit breakers to prevent excessive losses
- Document and validate kill switch behavior with brokers
- **New**: Implement pre-deployment validation as part of compliance checklist
- **New**: Monitor and report quota headroom for regulatory compliance

**Section sources**
- [event_store.py:183-211](file://ntrade/storage/event_store.py#L183-L211)
- [live_runner.py:181-196](file://ntrade/runner/live_runner.py#L181-L196)
- [pre_deploy_check.py:1-112](file://scripts/pre_deploy_check.py#L1-L112)

### Pre-Deployment Validation Commands
Execute the following commands before deploying to production:

```bash
# Basic pre-deploy check
python scripts/pre_deploy_check.py

# Strict mode (recommended for production)
python scripts/pre_deploy_check.py --strict

# Custom token path
python scripts/pre_deploy_check.py --token-path /path/to/token.json

# Individual component checks
python scripts/live_read_check.py --strict
python scripts/paper_gate_run.py
python scripts/live_smoke.py
```

**Section sources**
- [pre_deploy_check.py:75-107](file://scripts/pre_deploy_check.py#L75-L107)
- [live_read_check.py:90-95](file://scripts/live_read_check.py#L90-L95)