"""`$derived` states a dependency, and nothing else in the graph records one.

Svelte 5 replaces reactive statements with runes. `$state` and `$props` produce
bindings the walker already nodes, but `$derived(a + b)` additionally says which
state the value recomputes from — intra-component data flow that no import, call
or containment edge captures.

Modelled the way graphify already models Riverpod providers in Dart: the existing
`references` relation carrying a `context` discriminator, not a new relation name
(`extractors/dart.py` -> `add_edge(..., "references", context="riverpod_reference")`).
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_svelte


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _edges(result, relation):
    by_id = {n["id"]: n.get("label") for n in result["nodes"]}
    return {
        (by_id.get(e.get("source")), by_id.get(e.get("target")))
        for e in result["edges"]
        if e.get("relation") == relation and e.get("context") == "svelte_rune"
    }


def test_derived_records_which_state_it_recomputes_from(tmp_path):
    comp = _write(
        tmp_path / "Cart.svelte",
        """<script lang="ts">
  let count = $state(0)
  let price = $state(2)
  let unrelated = $state(9)
  let total = $derived(count * price)
</script>

<span>{total}</span>
""",
    )
    result = extract_svelte(comp)
    deps = _edges(result, "references")
    assert ("total", "count") in deps
    assert ("total", "price") in deps
    assert ("total", "unrelated") not in deps, "an unread binding became a dependency"
