"""A field's type reference must resolve when the type is declared in the same file.

`_ts_walk_class_members` reads `property_signature` / `public_field_definition`
and records a `references` use with `context="field"`. The use only becomes an
edge when the type arrived through an import; a type declared in the same file
is dropped, so a self-contained module — the normal TypeScript idiom — yields a
graph with no type-to-type structure at all.

Rust resolves the same-file case (`rust.py` struct fields), so the two languages
disagree on identical declarations.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _field_targets(result: dict) -> set[str]:
    by_id = {n["id"]: str(n.get("label") or "") for n in result["nodes"]}
    return {
        by_id.get(e.get("target"), "")
        for e in result["edges"]
        if e.get("relation") == "references" and e.get("context") == "field"
    }


def test_a_field_resolves_a_type_declared_in_the_same_file(tmp_path):
    src = _write(
        tmp_path / "model.ts",
        """export interface Photo {
  url: string;
}

export interface Model {
  photo: Photo;
}
""",
    )
    result = extract([src], root=tmp_path)
    assert "Photo" in _field_targets(result), (
        "same-file field type produced no references(field) edge"
    )


def test_an_imported_field_type_still_resolves(tmp_path):
    _write(tmp_path / "photo.ts", "export interface Photo { url: string; }\n")
    src = _write(
        tmp_path / "model.ts",
        """import type { Photo } from './photo';

export interface Model {
  photo: Photo;
}
""",
    )
    result = extract([tmp_path / "photo.ts", src], root=tmp_path)
    assert "Photo" in _field_targets(result)
