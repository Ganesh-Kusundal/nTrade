---
kind: design
name: PyWebView over Tauri/Electron/NiceGUI for local desktop shell
source: session
category: adr
---

# PyWebView over Tauri/Electron/NiceGUI for local desktop shell

_Source: coding plans from commit period 0b24409 → 8bd7b09 — records intent at planning time; the implementation may lag or differ._

**Status:** accepted

## Context
The UI needs a native desktop window to host the React frontend. Multiple packaging/shell options were considered for a local-only trading terminal.

## Decision drivers
- minimal binary size
- simple Python integration
- native OS windowing

## Considered options
- **NiceGUI (pure Python UI)** _(rejected)_ — pros: No JavaScript toolchain; cons: Ceiling on polish, limited chart/layout control, can't build professional trading terminal
- **Tauri + React (Rust shell)** _(rejected)_ — pros: Small binary, fast; cons: Rust toolchain adds complexity, migration path later if needed
- **Electron + React** _(rejected)_ — pros: Mature ecosystem; cons: 150MB+ binary, high memory usage, overkill for local-only app
- **PyWebView** — pros: Identical frontend experience with 5 lines of Python, minimal overhead, native OS windowing; cons: Depends on system webview runtime

## Decision
Use PyWebView to wrap the React/Vite frontend in a native desktop window. The launcher starts uvicorn in a daemon thread, health-checks /api/session/status before opening the window, and blocks on webview.start().

## Consequences
Single Python entry point (`python main.py`) launches the full application. Can migrate to Tauri later if Rust becomes desirable, but PyWebView provides identical frontend with minimal overhead.