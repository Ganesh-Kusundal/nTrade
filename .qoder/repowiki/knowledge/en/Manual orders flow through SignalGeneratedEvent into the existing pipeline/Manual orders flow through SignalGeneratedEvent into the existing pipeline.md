---
kind: design
name: Manual orders flow through SignalGeneratedEvent into the existing pipeline
source: session
category: adr
---

# Manual orders flow through SignalGeneratedEvent into the existing pipeline

_Source: coding plans from commit period 0b24409 → 8bd7b09 — records intent at planning time; the implementation may lag or differ._

**Status:** accepted

## Context
User-initiated order placement from the UI must go through the same risk checks (circuit breakers, limits, fat-finger guards) as automated strategies, without duplicating order-plumbing logic.

## Decision drivers
- reuse existing risk engine
- no duplicate order plumbing
- consistent order lifecycle

## Considered options
- **Call broker.place_order() directly from API route** _(rejected)_ — pros: Simple one-liner; cons: Bypasses RiskEngine entirely, no circuit breakers or limits applied
- **Publish SignalGeneratedEvent(strategy='manual') to kernel.bus** — pros: Reuses full pipeline: RiskEngine → OrderEngine → ExecutionRouter → BrokerExecution; all existing safeguards apply; cons: Slightly more indirection than direct call

## Decision
POST /api/orders publishes a SignalGeneratedEvent with strategy='manual' to the kernel's EventBus, flowing through the existing SignalApprovedEvent → OrderIntentEvent chain so risk checks and execution are identical to automated strategies.

## Consequences
Manual orders inherit all risk controls (halt/resume, daily loss limits, drawdown guards). No new order-plumbing code is needed in the API layer.