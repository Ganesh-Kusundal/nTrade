---
kind: external_dependency
name: GraphiZify Code Graph Tool
slug: graphify
category: external_dependency
category_hints:
    - other
scope:
    - '**'
source_files:
    - .agents/skills/graphify/SKILL.md
    - graphify-out/graph.json
---

### Identity
CLI tool for building interactive code graphs from Python source files.

### Role in this repo
Development/analysis tool only — not a runtime dependency. Used to generate architectural graphs showing module relationships, identify god-nodes (most connected classes), and query dependency paths.

### Usage
- `graphify update .` — rebuilds graph from current source
- `graphify explain "ClassName"` — shows node + connections
- `graphify path "A" "B"` — shortest path between two nodes
- `graphify god-nodes` — most connected architectural hubs
- Output artifacts: `graph.json`, `graph.html`, `GRAPH_REPORT.md`, `manifest.json`

### Current state
Installed under `.agents/skills/graphify/` with symlinks in `.claude/skills/`. Graph built with 2160 nodes, 5034 edges, 104 communities. Graph is frequently stale after code changes.