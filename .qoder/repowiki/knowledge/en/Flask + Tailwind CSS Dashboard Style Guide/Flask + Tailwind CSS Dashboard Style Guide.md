---
kind: frontend_style
name: Flask + Tailwind CSS Dashboard Style Guide
category: frontend_style
scope:
    - '**'
source_files:
    - .agents/skills/dhan-tradehull/references/flask-ui.md
    - .agents/skills/dhan-tradehull/references/ui-ux.md
---

This repository does not contain a built-in frontend application. The only UI guidance is documented in the TradeHull skill references under `.agents/skills/dhan-tradehull/references/`, which prescribe how to build an optional browser dashboard on top of a Python algo using Flask and Tailwind CSS via CDN — no build step, no framework churn.

**System/approach**: A minimal Flask server serves a single `templates/index.html` page that uses vanilla JavaScript (`fetch()`) to call JSON endpoints. Styling is done exclusively with Tailwind CSS loaded from the CDN (`<script src="https://cdn.tailwindcss.com"></script>`), so there is no local CSS file, no build pipeline, and no component library.

**Key files**:
- `.agents/skills/dhan-tradehull/references/flask-ui.md` — project layout, route patterns, progressive scanning, reverse-proxy path handling, deployment as a long-lived process.
- `.agents/skills/dhan-tradehull/references/ui-ux.md` — dashboard design rules: dark theme (`bg-slate-950`), colour semantics (emerald for bullish, rose for bearish, slate for neutral, amber for warnings), tabular numbers, right-aligned values, LIVE banner when real orders are enabled, progress bars, stop/kill-switch buttons, empty/error states written as sentences.

**Architecture & conventions**:
- Algo logic lives in a separate module (e.g. `scanner.py`); Flask (`app.py`) only renders templates and returns JSON — never mixes broker calls into routes.
- Pages use relative fetch paths (`window.location.pathname` + endpoint) so they work both directly and behind a reverse proxy.
- Development uses `Cache-Control: no-store` via `@app.after_request` to avoid stale HTML/JS.
- Long-running scans stream results one-by-one (`/scan_one`) rather than blocking; a Stop button acts as a kill switch.
- Dark theme is the default for trading screens; meaning is never encoded in colour alone — every state includes a word label.

**Constraints enforced by the references**:
- Always render every scanned row with the underlying values, not just the verdict.
- Show live progress (current symbol, count, bar, ticking counters).
- Provide a visible Stop button while any long-running or money-spending action runs.
- If the dashboard can place live orders, display a prominent LIVE banner driven by server config (exact qty/product/cap).
- Use `tabular-nums`, right-align numbers, round in the backend, and always show a data timestamp.
- Write empty/error states as full sentences, never leave blanks.