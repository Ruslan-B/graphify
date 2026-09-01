"""Two declarations in ONE file that differ only by case must both survive.

`normalize_id` is deliberately caseless-stable (an invariant with its own tests),
so `interface Tier` and `const tier` in the same module produce the same node ID.
`_disambiguate_colliding_node_ids` salts colliders with their source path, and
bails out when the colliders share one — which is exactly this case, so one node
silently overwrites the other.

Measured on the realms monorepo: 56 files carry such a pair. `Tier`/`tier` in
`apps/cloud/src/routes/hearth.ts` loses `tier`; `Receipt`/`receipt` in
`apps/cloud/scripts/apply-jobs.ts` loses `receipt`. The pairing is a TypeScript
convention (PascalCase type beside its camelCase value), not an oddity.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from graphify.extract import extract


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.xfail(
    reason="Known, unfixed: normalize_id is caseless by a documented invariant, and "
           "_disambiguate_colliding_node_ids only salts by source path, so it bails when "
           "the colliders share one. Splitting them after the fact cannot work either - "
           "edges that pointed at the shared id are ambiguous by construction. Fixing it "
           "is an ID-scheme decision, not a patch; until then the collapse is at least "
           "reported (see the test below).",
    strict=True,
)
def test_type_and_value_differing_only_by_case_both_reach_the_graph(tmp_path):
    src = _write(
        tmp_path / "hearth.ts",
        """export interface Tier {
  name: string
}

export const tier = resolve('chat')

function resolve(n: string): string {
  return n
}
""",
    )
    result = extract([src], root=tmp_path)
    labels = {str(n.get("label") or "") for n in result["nodes"]}
    assert "Tier" in labels, "the interface is missing"
    assert "tier" in labels, "the value is missing — the caseless ID collision ate it"
    ids = [n["id"] for n in result["nodes"] if n.get("label") in ("Tier", "tier")]
    assert len(set(ids)) == 2, f"both symbols share one id: {ids}"


def test_the_collapse_is_reported_instead_of_silent(tmp_path, capsys):
    """Losing a declaration is survivable; losing it silently is not.

    The graph cannot report completeness it does not have. Until the ID scheme
    itself is decided (caseless normalisation is a documented invariant with its
    own tests, so this is not a local patch), the collapse must at least be
    visible — the same way `dedup.py` already reports a cross-file collision.
    """
    src = _write(
        tmp_path / "hearth.ts",
        """export interface Tier {
  name: string
}

export const tier = resolve('chat')

function resolve(n: string): string {
  return n
}
""",
    )
    extract([src], root=tmp_path)
    err = capsys.readouterr().err
    assert "Tier" in err and "tier" in err, f"the collapse was not reported: {err!r}"
    assert "hearth.ts" in err
