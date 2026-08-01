---
kind: frontend_style
name: No Frontend Style System — Python Trading Framework with No UI Code
category: frontend_style
scope:
    - '**'
---

This repository is a pure Python trading framework (nTrade) focused on backtesting, live execution, market data feeds, and broker adapters. There is no frontend code, CSS, HTML templates, or styling system present in the source tree. The only references to UI are agent skill reference documents under `.agents/skills/dhan-tradehull/references/` that describe how to build optional Flask-based dashboards using Tailwind CSS via CDN for external algo projects — these are instructional guides, not part of the nTrade package itself. The core `ntrade/` package contains zero web or UI code; it is entirely backend Python.