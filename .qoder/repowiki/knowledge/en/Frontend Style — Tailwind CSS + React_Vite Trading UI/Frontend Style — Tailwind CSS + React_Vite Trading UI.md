---
kind: frontend_style
name: Frontend Style — Tailwind CSS + React/Vite Trading UI
category: frontend_style
scope:
    - '**'
source_files:
    - docs/superpowers/specs/2026-08-02-trading-ui-design.md
    - .agents/skills/dhan-tradehull/references/ui-ux.md
---

The nTrade repository contains a planned but not yet implemented frontend layer. The `ui/` directory exists only as an empty placeholder, and no CSS, SCSS, or theme files are present in the codebase. However, a comprehensive design specification (`docs/superpowers/specs/2026-08-02-trading-ui-design.md`) defines the intended styling system:

**Styling approach**: Tailwind CSS (v3+) via Vite plugin, used utility-first across a React 18 + TypeScript SPA built with Vite 5+. A secondary reference (`agencies/skills/dhan-tradehull/references/ui-ux.md`) documents an alternative lightweight approach using Tailwind CSS loaded directly from CDN for rapid prototyping without a build step.

**Design tokens & theming**: Dark theme is the default (`bg-slate-950`), with semantic color mappings: emerald for bullish/buy states, rose for bearish/sell/warning states, slate for neutral, amber for warnings. Numbers use `tabular-nums` and right-alignment; text uses left-alignment. All state meanings must be conveyed by both color and explicit labels (never color alone).

**Layout conventions**: CSS Grid for responsive panel layouts; a collapsible left sidebar for navigation; summary tiles at the top, followed by filters/search, then data tables. Live trading indicators use prominent rose-bordered banners driven from server configuration.

**Component library references**: TradingView lightweight-charts for candlestick charts, AG Grid for real-time data tables, Zustand for state management, react-router for navigation. No custom component library is defined — components are built inline with Tailwind utilities.

**Build pipeline**: Development runs separate Vite dev server (HMR) and FastAPI backend; production builds static assets served by FastAPI through PyWebView desktop window.

No actual frontend source code has been committed yet — this represents the approved architectural plan for the UI layer.