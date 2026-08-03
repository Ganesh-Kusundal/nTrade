---
kind: design
name: Zero kernel modification — new UI in api/ and ui/ only
source: session
category: adr
---

# Zero kernel modification — new UI in api/ and ui/ only

_Source: coding plans from commit period 0b24409 → 8bd7b09 — records intent at planning time; the implementation may lag or differ._

**Status:** accepted

## Context
The nTrade kernel has 638+ existing tests and a complex event-driven architecture. Adding a trading UI must not risk breaking the core or requiring changes to the kernel package.

## Decision drivers
- protect existing test suite
- zero-risk integration
- separate concerns between kernel and UI

## Considered options
- **Modify ntrade/ kernel directly** _(rejected)_ — pros: Direct access to internals, no bridge needed; cons: Breaks 638+ tests, couples UI to kernel internals, harder to maintain
- **New code in api/, ui/, main.py, tests/test_api_*.py only** — pros: Kernel untouched, existing tests stay green, clean separation of concerns; cons: Requires bridge layer to communicate with kernel via public APIs

## Decision
All new code lives outside the ntrade/ package: FastAPI server in api/, React frontend in ui/, launcher in main.py, and tests in tests/test_api_*.py. The kernel remains completely unmodified.

## Consequences
A KernelBridge is required to translate between REST/WebSocket and the kernel's EventBus. This adds an indirection layer but preserves the integrity of the existing kernel and its test suite.