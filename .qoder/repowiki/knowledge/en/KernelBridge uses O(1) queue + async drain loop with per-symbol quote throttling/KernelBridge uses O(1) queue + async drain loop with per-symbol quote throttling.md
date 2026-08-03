---
kind: design
name: KernelBridge uses O(1) queue + async drain loop with per-symbol quote throttling
source: session
category: adr
---

# KernelBridge uses O(1) queue + async drain loop with per-symbol quote throttling

_Source: coding plans from commit period 0b24409 → 8bd7b09 — records intent at planning time; the implementation may lag or differ._

**Status:** accepted

## Context
The kernel's EventBus is synchronous under RLock (event_bus.py L57), so any handler must be O(1) to avoid blocking kernel dispatch. Quote events amplify 1 tick into 5-15 downstream events, risking WebSocket floods.

## Decision drivers
- non-blocking kernel dispatch
- handle event amplification
- bounded memory usage

## Considered options
- **Broadcast immediately on event callback** _(rejected)_ — pros: Lowest latency; cons: Blocks kernel's RLock, risks deadlock on slow I/O, no rate limiting
- **queue.Queue.put_nowait() + async drain_loop with 10Hz per-symbol throttle** — pros: O(1) handler (~100ns), decouples kernel from I/O, prevents WebSocket floods, last-value-wins for quotes; cons: Slight delay (drain loop runs at ~20Hz)

## Decision
KernelBridge subscribes to specific UI-relevant event types (QuoteUpdatedEvent, OrderAcceptedEvent, etc.) and enqueues them via queue.Queue.put_nowait(). An async drain_loop serializes and broadcasts at ~20Hz, with per-symbol quote throttling capped at 10Hz using time.monotonic() checks.

## Consequences
Kernel dispatch is never blocked by I/O. Quote storms are absorbed by throttling. Frontend uses requestAnimationFrame batching to handle high-frequency updates without render storms.