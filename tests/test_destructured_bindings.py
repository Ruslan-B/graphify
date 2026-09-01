"""A destructuring declaration binds N names, so it should node N times.

`_read_text` on an `object_pattern` returns the whole pattern as one string, so
`const { a, b } = f()` became a single node labelled `{ a, b }` — unusable as a
label, unusable as an id, and it hides both bindings. Svelte 5 makes this the
normal shape of a component's public surface: every component declares its props
as `let { ... } = $props()`, so on the realms monorepo 19 components each had one
blob node in place of their props.

`_js_collect_pattern_idents` already walks these patterns correctly for scope
tracking - it recurses destructuring, takes the left of `x = default`, takes the
local name of `{ a: local }`, and skips type annotations.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_destructured_module_bindings_become_one_node_each(tmp_path):
    src = _write(
        tmp_path / "props.ts",
        """export const { photos, index = 0, open: isOpen } = makeProps()

function makeProps() {
  return { photos: [], index: 0, open: false }
}
""",
    )
    result = extract([src], root=tmp_path)
    labels = {str(n.get("label") or "") for n in result["nodes"]}
    assert {"photos", "index", "isOpen"} <= labels, f"bindings missing: {sorted(labels)}"
    blobs = [x for x in labels if x.startswith("{")]
    assert not blobs, f"the whole pattern is still a single node: {blobs}"


def test_a_default_value_in_an_object_pattern_is_not_a_binding(tmp_path):
    """`_js_collect_pattern_idents` promises it never walks the default-value side.

    It keeps that promise for `assignment_pattern` (`function f(x = g())`) but an
    object pattern uses `object_assignment_pattern`, which the branch does not
    name — so `{ index = bind() }` fell through to the generic recursion and
    bound `bind`. Svelte 5 writes exactly this shape:
    `let { index = $bindable() } = $props()`, which minted a `$bindable` node.
    """
    src = _write(
        tmp_path / "opts.ts",
        """export const { index = bind(), open } = makeOpts()

function bind() { return 0 }
function makeOpts() { return { open: false } }
""",
    )
    result = extract([src], root=tmp_path)
    labels = {str(n.get("label") or "") for n in result["nodes"]}
    assert "index" in labels and "open" in labels
    assert "bind" not in labels, "the default-value callee was bound as a name"


def test_a_destructured_require_is_an_import_not_a_declaration(tmp_path):
    """`const { doWork } = require('./lib')` binds an entity ANOTHER file defines.

    Noding the binding here mints a local rival that captures the cross-file
    `calls` edge meant for the real definition — which is what
    `test_cross_file_call_promoted_to_extracted_with_import_evidence` catches.
    The CJS import branch already handles these; the pattern branch must not.
    """
    caller = _write(tmp_path / "caller.js",
                    "const { doWork } = require('./lib');\nfunction run() { doWork(); }\n")
    callee = _write(tmp_path / "lib.js",
                    "function doWork() { return 1; }\nmodule.exports = { doWork };\n")
    result = extract([caller, callee], root=tmp_path)
    caller_labels = {
        str(n.get("label") or "")
        for n in result["nodes"]
        if "caller" in str(n.get("source_file") or "")
    }
    assert "doWork" not in caller_labels, "the imported binding was noded locally"
    assert "run()" in caller_labels
