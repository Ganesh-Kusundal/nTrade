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
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document provides production deployment examples and operational guidance for nTrade applications. It covers containerization, environment configuration, monitoring setup, latency benchmarking, performance optimization, error handling, logging, alerting, health checks, graceful shutdown, crash recovery, scaling considerations, resource management, memory optimization for high-frequency trading, dashboards/metrics collection, profiling, security best practices, secret management, and compliance considerations. The guidance is grounded in the repository’s architecture and runtime components to ensure practical, code-aligned operations.

## Project Structure
nTrade follows a layered, event-centric architecture with clear separation between domain, execution, broker adapters, and infrastructure. For production, you will orchestrate:
- A TradingKernel (live or replay mode)
- A MarketFeedSource (synthetic or live Dhan feed)
- A LiveRunner loop that polls orders, syncs positions, monitors feed health, and triggers risk kill switches
- An EventStore for audit and crash recovery
- Authentication providers for secure broker access

```mermaid
graph TB
subgraph "Runtime"
LR["LiveRunner"]
K["TradingKernel"]
FEED["MarketFeedSource<br/>Synthetic/Dhan"]
STORE["EventStore"]
AUTH["Dhan Auth Provider"]
end
LR --> K
LR --> FEED
K --> STORE
K --> AUTH
FEED --> K
```

**Diagram sources**
- [live_runner.py:1-196](file://ntrade/runner/live_runner.py#L1-L196)
- [resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)
- [event_store.py:1-236](file://ntrade/storage/event_store.py#L1-L236)
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)

**Section sources**
- [ARCHITECTURE.md:1-389](file://ARCHITECTURE.md#L1-L389)
- [pyproject.toml:1-25](file://pyproject.toml#L1-L25)

## Core Components
- LiveRunner: Orchestrates kernel lifecycle, feed warmup, periodic polling/sync, heartbeat, watchdog, and risk-triggered kill switch activation.
- ResilientKernel: Extends TradingKernel with deterministic crash recovery from EventStore causal stream without re-trading.
- EventStore: Append-only JSONL record of events; supports market-only replay, open-order delta reconstruction, and recovery event ordering.
- Dhan Auth Provider: Manages token lifecycle, shared store usage, proactive refresh, and PIN+TOTP fallback.

Key operational behaviors:
- Feed warmup ensures connectivity before starting strategies.
- Heartbeat and watchdog detect frozen feeds and halt safely.
- Risk engine halts trigger broker kill switches across all instruments.
- Crash recovery rebuilds state deterministically and resumes safely.

**Section sources**
- [live_runner.py:1-196](file://ntrade/runner/live_runner.py#L1-L196)
- [resilient.py:1-147](file://ntrade/kernel/resilient.py#L1-L147)
- [event_store.py:1-236](file://ntrade/storage/event_store.py#L1-L236)
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)

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
```

**Diagram sources**
- [live_runner.py:55-141](file://ntrade/runner/live_runner.py#L55-L141)
- [live_runner_run.py:26-64](file://scripts/live_runner_run.py#L26-L64)

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

## Dependency Analysis
Production dependencies include Python packages defined in the project manifest and runtime integrations with Dhan-Tradehull. Secrets are excluded from version control via .gitignore.

```mermaid
graph TB
Pkg["pyproject.toml"] --> Deps["pandas, numpy, python-dotenv,<br/>Dhan-Tradehull"]
Git[".gitignore"] --> Secrets[".env, Dependencies/, caches,<br/>.freebuff/, .pytest_cache/, .benchmarks/"]
```

**Diagram sources**
- [pyproject.toml:1-25](file://pyproject.toml#L1-L25)
- [.gitignore:1-25](file://.gitignore#L1-L25)

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

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Feed warmup failure: verify connectivity and minimum ticks; inspect logs for connection errors.
- Frozen feed: watchdog detects no new ticks; risk halt triggers automatically; check network and broker status.
- Kill switch failure: ensure broker adapter supports kill_switch capability; log critical failures and verify broker-side enforcement.
- OOM in live sessions: cap EventBus history length; offload heavy analytics to async workers.
- Token expiry: use PIN+TOTP auth to avoid daily restarts; monitor JWT expiry and proactive refresh scheduling.
- Port conflicts: kill by port rather than process name; confirm PID ownership after restart.

Operational tips:
- Use systemd for auto-restart and boot persistence.
- Follow logs with journalctl or tail -f app.log.
- Validate new deployments by running synthetic rehearsals first.

**Section sources**
- [deployment.md:51-118](file://.agents/skills/dhan-tradehull/references/deployment.md#L51-L118)
- [live_runner.py:154-174](file://ntrade/runner/live_runner.py#L154-L174)
- [dhan_auth_provider.py:58-74](file://ntrade/brokers/dhan_auth_provider.py#L58-L74)

## Conclusion
nTrade’s event-centric design, resilient kernel, and robust authentication provide a solid foundation for production deployments. By leveraging the LiveRunner orchestration, EventStore-based audit and recovery, and structured benchmarking tooling, teams can deploy scalable, observable, and safe trading systems. Adhering to the operational guidance here ensures reliable live trading, efficient resource usage, and strong security posture.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Containerization Example
- Base image: Python 3.10+ with system dependencies for pandas/numpy
- Install package from pyproject.toml
- Mount secrets via environment variables or mounted volumes (.env excluded from repo)
- Run scripts/live_runner_run.py in synth mode for rehearsal; switch to live mode with proper credentials
- Use health checks to probe heartbeat events or a lightweight HTTP endpoint if exposed

[No sources needed since this section provides general guidance]

### Environment Configuration
- Required environment variables: DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN (optional), DHAN_PIN, DHAN_TOTP_SECRET, DHAN_TOKEN_PATH, DHAN_COOLDOWN_PATH
- Use .env for local development; inject secrets at runtime in production
- Validate token expiry and proactive refresh behavior

**Section sources**
- [dhan_auth.py:114-151](file://ntrade/brokers/dhan_auth.py#L114-L151)

### Monitoring and Alerting
- HeartbeatEvent: emit periodically with tick counts and open orders
- Watchdog: trigger RiskHaltedEvent on frozen feed
- Metrics: collect events_per_sec, ticks_per_sec, fanout from benchmarks
- Dashboards: visualize latency.json trends, heartbeat metrics, and risk events
- Alerting: on RiskHaltedEvent, feed watchdog trips, or kill switch failures

**Section sources**
- [live_runner.py:143-174](file://ntrade/runner/live_runner.py#L143-L174)
- [benchmark_latency.py:18-32](file://scripts/benchmark_latency.py#L18-L32)

### Security Best Practices
- Never commit secrets; enforce .gitignore rules
- Use PIN+TOTP auth for long-running services
- Restrict broker API access to whitelisted IPs
- Limit process privileges and isolate containers
- Rotate tokens proactively and monitor expiry

**Section sources**
- [.gitignore:1-25](file://.gitignore#L1-L25)
- [dhan_auth_provider.py:40-74](file://ntrade/brokers/dhan_auth_provider.py#L40-L74)

### Compliance Considerations
- Maintain audit trails via EventStore JSONL
- Ensure deterministic replay for reproducibility and debugging
- Enforce risk limits and circuit breakers to prevent excessive losses
- Document and validate kill switch behavior with brokers

**Section sources**
- [event_store.py:183-211](file://ntrade/storage/event_store.py#L183-L211)
- [live_runner.py:181-196](file://ntrade/runner/live_runner.py#L181-L196)