"""A graph must record which build produced it.

`built_at_commit` names the commit of the SCANNED repo. Nothing names the
extractor, so a graph written by a dev checkout and one written by the released
wheel are byte-indistinguishable — and an extractor change (a new construct, a
fixed resolution rule) silently makes every existing graphify-out/ stale with no
way to tell.

The distinction that matters is release vs working checkout, so `built_by`
carries the package version and, when the package is imported from a git work
tree, that tree's HEAD and whether it was dirty.
"""
from __future__ import annotations

import json

import networkx as nx

from graphify.export import to_json


def _tiny_graph():
    G = nx.Graph()
    G.add_node("a", label="A", type="code")
    G.add_node("b", label="B", type="code")
    G.add_edge("a", "b", relation="calls")
    return G


def test_the_graph_records_the_build_that_wrote_it(tmp_path):
    out = tmp_path / "graph.json"
    to_json(_tiny_graph(), {0: ["a", "b"]}, str(out), force=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "built_by" in data, "the graph does not say what built it"
    assert data["built_by"].startswith("graphifyy "), data["built_by"]


def test_a_git_checkout_is_distinguishable_from_a_release(tmp_path):
    """graphify imported from its own work tree must name that tree's HEAD."""
    from graphify.export import _built_by

    stamp = _built_by()
    # This suite runs from the checkout, so the stamp must carry a revision.
    assert "(" in stamp and ")" in stamp, (
        f"a checkout build must name its revision, got {stamp!r}"
    )
