---
kind: frontend_style
name: No Frontend Style System
category: frontend_style
scope:
    - '**'
---

This repository is a Python trading framework with no frontend code. The entire codebase under `ntrade/` consists of Python modules implementing domain models, an event-driven kernel, broker adapters (Dhan/paper), execution targets, backtesting/replay engines, and orchestration scripts. There are no CSS, SCSS, Tailwind, HTML templates, JavaScript, or any other frontend asset files present in the source tree.

The only references to UI styling found are within agent skill reference documentation under `.agents/skills/dhan-tradehull/references/flask-ui.md` and `.agents/skills/dhan-tradehull/references/ui-ux.md`, which describe how to optionally build a Flask-based browser dashboard on top of algorithms. These are instructional references for AI agents, not actual implementation code in this repository.