"""Re-apply persisted edge resolutions onto graphify-out/graph.json.

graphify regenerates graph.json from extraction on every full build or
--update, which wipes manual edge relabels (AMBIGUOUS -> EXTRACTED etc.).
This script re-applies the durable resolutions recorded in
graphify-out/resolutions.json so resolved edges survive rebuilds.

Two rebuild failure modes are handled:
- a link is dropped even though both endpoints survive -> the link is
  re-created from the persisted resolution;
- an endpoint node is renamed/replaced -> the resolution cannot be applied,
  reported to stderr, and the process exits non-zero so the caller notices.

Idempotent: safe to run any time after a rebuild; re-running is a no-op.

Usage:
    python scripts/apply_graph_resolutions.py            # defaults to graphify-out/
    python scripts/apply_graph_resolutions.py graphify-out
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_FIELDS = (
    "relation", "confidence", "confidence_score",
    "source_file", "source_location",
)


def main(out_dir: str = "graphify-out") -> int:
    out = Path(out_dir)
    graph_path = out / "graph.json"
    resolutions_path = out / "resolutions.json"

    if not graph_path.exists():
        print(f"apply_graph_resolutions: {graph_path} not found — nothing to do")
        return 0
    if not resolutions_path.exists():
        print(f"apply_graph_resolutions: {resolutions_path} not found — nothing to do")
        return 0

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    resolutions = json.loads(resolutions_path.read_text(encoding="utf-8"))
    entries = resolutions.get("resolutions", [])
    if not entries:
        print("apply_graph_resolutions: no resolutions recorded — nothing to do")
        return 0

    node_ids = {n["id"] for n in graph.get("nodes", [])}
    links = graph.setdefault("links", [])

    applied, unapplied = 0, 0
    for res in entries:
        key = (res.get("source"), res.get("target"))
        link = next((l for l in links if (l.get("source"), l.get("target")) == key), None)
        if link is None:
            if key[0] in node_ids and key[1] in node_ids:
                # Rebuild dropped the link while both endpoints survived:
                # re-create it from the persisted resolution.
                link = {"source": key[0], "target": key[1], "weight": 1.0}
                links.append(link)
            else:
                unapplied += 1
                print(
                    f"apply_graph_resolutions: UNAPPLIED {key[0]} -> {key[1]} "
                    "(endpoint node(s) missing after rebuild)", file=sys.stderr,
                )
                continue
        link.update({k: res[k] for k in _FIELDS if k in res})
        # Audit trail: distinguish manual resolutions from AST/semantic edges.
        link["_origin"] = "resolved"
        applied += 1

    if applied:
        serialized = json.dumps(graph, ensure_ascii=False)
        if graph_path.read_text(encoding="utf-8") != serialized:
            graph_path.write_text(serialized, encoding="utf-8")
    print(f"apply_graph_resolutions: applied {applied}/{len(entries)} resolutions to {graph_path}")
    if unapplied:
        print(
            f"apply_graph_resolutions: {unapplied} resolution(s) could not be applied — "
            "stale entries may need pruning from resolutions.json", file=sys.stderr,
        )
    return 1 if unapplied else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "graphify-out"))
