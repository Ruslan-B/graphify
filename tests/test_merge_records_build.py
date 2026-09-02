"""`merge-graphs` must stamp its output the way `to_json` stamps a repo graph.

The merged graph is the one an MCP server is usually pointed at, so it is the
artifact most in need of saying which build produced it — and it is written by a
separate path that mirrors `to_json`'s shape by hand ("so every writer agrees",
cli.py). A stamp on one writer and not the other is the disagreement that comment
exists to prevent.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PYTHON = sys.executable


def _write(p: Path, nodes: list[dict]):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "directed": True, "multigraph": False, "graph": {},
        "nodes": nodes, "links": [],
    }), encoding="utf-8")


def test_the_merged_graph_records_the_build_that_wrote_it(tmp_path):
    a = tmp_path / "svc_a" / "graphify-out" / "graph.json"
    b = tmp_path / "svc_b" / "graphify-out" / "graph.json"
    _write(a, [{"id": "x", "label": "X", "source_file": "x.ts"}])
    _write(b, [{"id": "y", "label": "Y", "source_file": "y.ts"}])
    out = tmp_path / "merged.json"

    r = subprocess.run(
        [PYTHON, "-m", "graphify", "merge-graphs", str(a), str(b), "--out", str(out)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert out.exists(), r.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "built_by" in data, "the merged graph does not say what built it"
    assert data["built_by"].startswith("graphifyy "), data["built_by"]
