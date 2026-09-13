"""C# cross-file resolution.

The config-driven C# *extractor* (``extract_csharp`` → ``_extract_generic``)
still lives in ``graphify/extract.py``; per ``extractors/MIGRATION.md`` the
config-driven languages cannot be ported one-by-one until the shared
``_extract_generic`` core moves as its own coordinated batch. This module is
the C# home for the parts that *are* cleanly separable — today, the cross-file
type-reference resolver below — and is where ``extract_csharp`` will land when
the core migration happens.
"""
from __future__ import annotations

import html
from pathlib import Path

from graphify.extractors.base import _make_id


def _build_csharp_type_def_index(all_nodes: list[dict]) -> dict[tuple[str, str], str]:
    """Return deterministic ``(namespace, name) -> node_id`` C# type definitions."""
    candidates: dict[tuple[str, str], list[dict]] = {}
    for node in all_nodes:
        if node.get("type") == "namespace":
            continue
        metadata = node.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("is_nested_type"):
            continue
        nid = node.get("id")
        label = node.get("label")
        if not (isinstance(nid, str) and nid and isinstance(label, str) and label):
            continue
        source_file = node.get("source_file")
        if (
            not isinstance(source_file, str)
            or not source_file.endswith(".cs")
            or node.get("file_type") != "code"
        ):
            continue
        if label.endswith(")") or label.startswith(".") or "." in label:
            continue
        namespace = metadata.get("namespace", "")
        if not isinstance(namespace, str):
            namespace = ""
        candidates.setdefault((namespace, label), []).append(node)

    return {
        key: sorted(
            nodes,
            key=lambda node: (
                str(node.get("source_file") or ""),
                str(node.get("source_location") or ""),
                str(node.get("id") or ""),
            ),
        )[0]["id"]
        for key, nodes in candidates.items()
    }


def _strip_trailing_csharp_generic_args(target_fqn: str) -> str:
    target_fqn = target_fqn.strip()
    if not target_fqn.endswith(">"):
        return target_fqn
    depth = 0
    for index in range(len(target_fqn) - 1, -1, -1):
        char = target_fqn[index]
        if char == ">":
            depth += 1
        elif char == "<":
            depth -= 1
            if depth == 0:
                return target_fqn[:index].strip()
    return target_fqn


def _resolve_cross_file_csharp_imports(
    per_file: list[dict],
    paths: list[Path],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Re-point resolvable C# ``using`` import edges to canonical internal nodes.

    Namespace imports resolve only to canonical C# namespace nodes. Alias imports
    resolve only when the alias target's prefix is a known canonical namespace and
    the simple type name exists in the shared C# type-definition index. ``using
    static`` and nested type aliases remain deliberate gaps because they need
    member/nested-type modeling beyond this import pass.
    """
    _ = (per_file, paths)
    namespace_id_by_label: dict[str, str] = {}
    for node in sorted(
        all_nodes,
        key=lambda node: (
            str(node.get("source_file") or ""),
            str(node.get("source_location") or ""),
            str(node.get("id") or ""),
        ),
    ):
        if node.get("type") != "namespace":
            continue
        label = node.get("label")
        nid = node.get("id")
        if isinstance(label, str) and label and isinstance(nid, str) and nid:
            namespace_id_by_label.setdefault(label, nid)

    type_def_index = _build_csharp_type_def_index(all_nodes)
    if not namespace_id_by_label and not type_def_index:
        return

    repointed_from: set[str] = set()
    for edge in all_edges:
        if edge.get("relation") != "imports":
            continue
        metadata = edge.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        using_kind = metadata.get("using_kind")
        target_fqn = metadata.get("target_fqn")
        if not using_kind or not isinstance(target_fqn, str) or not target_fqn:
            continue

        resolved = None
        if using_kind == "namespace":
            resolved = namespace_id_by_label.get(target_fqn)
        elif using_kind == "alias":
            base_fqn = _strip_trailing_csharp_generic_args(html.unescape(target_fqn))
            prefix, sep, name = base_fqn.rpartition(".")
            if sep and prefix in namespace_id_by_label:
                resolved = type_def_index.get((prefix, name))

        old_target = edge.get("target")
        if resolved and resolved != old_target:
            edge["target"] = resolved
            if isinstance(old_target, str) and old_target:
                repointed_from.add(old_target)

    if not repointed_from:
        return

    still_referenced: set[str] = set()
    for edge in all_edges:
        still_referenced.add(edge.get("source"))
        still_referenced.add(edge.get("target"))
    all_nodes[:] = [
        node for node in all_nodes
        if node.get("id") not in repointed_from or node.get("id") in still_referenced
    ]


_DOTNET_SOURCE_EXTS = (".cs", ".razor", ".cshtml")


def _is_cs_file(value: object) -> bool:
    return isinstance(value, str) and value.endswith(".cs")


def _is_dotnet_source_file(value: object) -> bool:
    return isinstance(value, str) and value.endswith(_DOTNET_SOURCE_EXTS)


def _metadata(value: object) -> dict:
    return value if isinstance(value, dict) else {}


class CsharpNameResolver:
    """Namespace/using/alias-aware C# simple-name resolution.

    Factored out of ``_resolve_csharp_type_references`` so the member-call pass
    (``_resolve_csharp_member_calls`` in ``graphify/extract.py``) can type a
    receiver with the same scoping rules the type-reference side already applies
    (the core of #1620), instead of bailing on any corpus-wide bare-name clash.

    Built from graph-stamped facts only: the ``(namespace, name)`` type-definition
    index plus the per-file ``using``/alias import edges (each carrying its lexical
    scope — ``("file", None)`` applies file-wide; ``("namespace", scope_id)``
    applies only where ``scope_id`` is in the referencing node's ``scope_chain``).
    """

    def __init__(self, all_nodes: list[dict], all_edges: list[dict]) -> None:
        self.node_by_id: dict[str, dict] = {
            node["id"]: node
            for node in all_nodes
            if isinstance(node.get("id"), str) and node.get("id")
        }
        self.type_def_index = _build_csharp_type_def_index(all_nodes)
        self.known_namespaces = {
            node.get("label")
            for node in all_nodes
            if node.get("type") == "namespace" and isinstance(node.get("label"), str)
        }

        self.namespace_usings_by_file: dict[str, list[tuple[str, str, str | None]]] = {}
        self.aliases_by_file: dict[str, dict[str, list[tuple[str, str, str | None]]]] = {}

        for edge in all_edges:
            if edge.get("relation") != "imports":
                continue
            source_node = self.node_by_id.get(edge.get("source"))
            if not (
                source_node
                and isinstance(source_node.get("label"), str)
                and source_node.get("label", "").endswith(_DOTNET_SOURCE_EXTS)
            ):
                continue
            source_file = source_node.get("source_file")
            if not _is_dotnet_source_file(source_file):
                continue
            metadata = _metadata(edge.get("metadata"))
            target_fqn = metadata.get("target_fqn")
            if not isinstance(target_fqn, str) or not target_fqn:
                continue
            scope_kind = metadata.get("scope_kind") or "file"
            scope_id = metadata.get("scope_id")
            using_kind = metadata.get("using_kind")
            if using_kind == "namespace":
                entry = (target_fqn, scope_kind, scope_id)
                bucket = self.namespace_usings_by_file.setdefault(source_file, [])
                if entry not in bucket:
                    bucket.append(entry)
            elif using_kind == "alias":
                alias = metadata.get("alias")
                if isinstance(alias, str) and alias:
                    entry = (target_fqn, scope_kind, scope_id)
                    bucket = self.aliases_by_file.setdefault(source_file, {}).setdefault(alias, [])
                    if entry not in bucket:
                        bucket.append(entry)

    @staticmethod
    def _namespace(node: dict | None) -> str:
        metadata = _metadata((node or {}).get("metadata"))
        namespace = metadata.get("namespace", "")
        return namespace if isinstance(namespace, str) else ""

    @staticmethod
    def _scope_chain(node: dict) -> list[str]:
        chain = _metadata(node.get("metadata")).get("scope_chain")
        return chain if isinstance(chain, list) else []

    def _using_in_scope(self, scope_kind: str, scope_id: str | None, source_node: dict) -> bool:
        if scope_kind == "file":
            return True
        return scope_id is not None and scope_id in self._scope_chain(source_node)

    def _scopes_for(self, source_node: dict, source_file: str) -> list[str]:
        def _append_unique(items: list[str], value: str) -> None:
            if value not in items:
                items.append(value)

        scopes: list[str] = []
        _append_unique(scopes, self._namespace(source_node))
        _append_unique(scopes, "")
        for namespace, scope_kind, scope_id in self.namespace_usings_by_file.get(source_file, []):
            if self._using_in_scope(scope_kind, scope_id, source_node):
                _append_unique(scopes, namespace)
        return scopes

    def _resolve_alias(self, label: str, source_node: dict, source_file: str) -> str | None:
        hits = set()
        for target_fqn, scope_kind, scope_id in self.aliases_by_file.get(source_file, {}).get(label, []):
            if not self._using_in_scope(scope_kind, scope_id, source_node):
                continue
            base_fqn = _strip_trailing_csharp_generic_args(html.unescape(target_fqn))
            namespace, sep, simple_name = base_fqn.rpartition(".")
            if not sep:
                simple_name = namespace
                namespace = ""
            if not simple_name:
                continue
            hit = self.type_def_index.get((namespace, simple_name))
            if hit:
                hits.add(hit)
        return next(iter(hits)) if len(hits) == 1 else None

    def resolve_type_name(
        self, label: str, source_node: dict, source_file: str
    ) -> tuple[str | None, bool]:
        """Resolve a simple type name to a definition node id, with a verdict.

        Returns ``(node_id, decisive)``:
          * ``(nid, True)`` — exactly one in-scope definition (or alias target).
          * ``(None, True)`` — the name is claimed by this file's scoping (an
            alias, or >1 in-scope candidates): genuinely ambiguous/unresolvable,
            the caller must NOT fall back to a looser corpus-wide match.
          * ``(None, False)`` — scoping knows nothing about the name; a caller
            may fall back (e.g. to the corpus-wide unique bare-name match).
        """
        if label in self.aliases_by_file.get(source_file, {}):
            return self._resolve_alias(label, source_node, source_file), True
        candidates: list[str] = []
        for namespace in self._scopes_for(source_node, source_file):
            hit = self.type_def_index.get((namespace, label))
            if hit and hit not in candidates:
                candidates.append(hit)
        if len(candidates) == 1:
            return candidates[0], True
        return None, bool(candidates)

    def resolve_label(self, label: str, source_node: dict, source_file: str) -> str | None:
        resolved, _decisive = self.resolve_type_name(label, source_node, source_file)
        return resolved

    def resolve_qualified(
        self, label: str, qualifier: object, source_node: dict, source_file: str
    ) -> str | None:
        # Sound qualified resolution: an in-scope alias for Q shadows the namespace Q. For a qualified
        # ref Q.label, look up (alias_target_namespace, label). If no in-scope alias, fall through to an
        # exact known namespace. Dangle on ambiguity / no hit / unknown qualifier.
        if not isinstance(qualifier, str) or not qualifier:
            return None
        in_scope = [
            entry for entry in self.aliases_by_file.get(source_file, {}).get(qualifier, [])
            if self._using_in_scope(entry[1], entry[2], source_node)
        ]
        if in_scope:
            hits = set()
            for target_fqn, _scope_kind, _scope_id in in_scope:
                alias_ns = _strip_trailing_csharp_generic_args(html.unescape(target_fqn))
                hit = self.type_def_index.get((alias_ns, label))
                if hit:
                    hits.add(hit)
            return next(iter(hits)) if len(hits) == 1 else None
        if qualifier in self.known_namespaces:
            return self.type_def_index.get((qualifier, label))
        return None


def _resolve_csharp_type_references(
    per_file: list[dict],
    paths: list[Path],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Arbitrate all C# ``inherits``/``implements``/``references`` targets.

    The extractor emits provisional same-file bindings and sourceless stubs. This
    pass is the single soundness gate: it uses only graph-stamped namespace/import
    facts, keeps a binding only when the referenced simple name resolves to one
    in-scope real type definition, and otherwise leaves the edge on a dangling stub.
    """
    _ = (per_file, paths)

    resolver = CsharpNameResolver(all_nodes, all_edges)
    node_by_id = resolver.node_by_id
    aliases_by_file = resolver.aliases_by_file

    def _resolve_label(label: str, source_node: dict, source_file: str) -> str | None:
        return resolver.resolve_label(label, source_node, source_file)

    def _resolve_qualified(label: str, qualifier: object, source_node: dict, source_file: str) -> str | None:
        return resolver.resolve_qualified(label, qualifier, source_node, source_file)

    def _is_placeholder(node: dict | None) -> bool:
        return bool(node) and not node.get("source_file")

    def _is_csharp_relevant_target(node: dict) -> bool:
        if node.get("type") == "namespace":
            return True
        source_file = node.get("source_file")
        return not source_file or _is_cs_file(source_file)

    def _label_for_type_ref_target(target_node: dict, source_file: str) -> str | None:
        label = target_node.get("label")
        if not isinstance(label, str) or not label:
            return None
        if not label.endswith(".cs"):
            return label

        stem = label[:-3]
        for alias in aliases_by_file.get(source_file, {}):
            if alias.lower() == stem.lower() or _make_id(alias) == _make_id(stem):
                return alias
        return stem or None

    def _dangling_stub_id(label: str, current_target: object) -> str:
        current = node_by_id.get(current_target)
        if _is_placeholder(current) and current.get("label") == label:
            return str(current_target)

        for node in all_nodes:
            nid = node.get("id")
            if (
                isinstance(nid, str)
                and node.get("label") == label
                and _is_placeholder(node)
            ):
                return nid

        stem = _make_id(label)
        stub_id = stem
        if stub_id in node_by_id:
            stub_id = _make_id("csharp_type_ref", label)
            suffix = 2
            while stub_id in node_by_id:
                stub_id = _make_id("csharp_type_ref", label, str(suffix))
                suffix += 1
        node = {
            "id": stub_id,
            "label": label,
            "file_type": "code",
            "source_file": "",
            "source_location": "",
        }
        all_nodes.append(node)
        node_by_id[stub_id] = node
        return stub_id

    REPOINT_RELATIONS = {"implements", "inherits", "references"}
    repointed_from: set[str] = set()
    for edge in all_edges:
        if edge.get("relation") not in REPOINT_RELATIONS:
            continue
        source_file = edge.get("source_file")
        if not _is_dotnet_source_file(source_file):
            continue
        source_node = node_by_id.get(edge.get("source"))
        target_node = node_by_id.get(edge.get("target"))
        if not source_node or not target_node:
            continue
        if not _is_csharp_relevant_target(target_node):
            continue
        metadata = _metadata(edge.get("metadata"))
        label = metadata.get("ref_token") or _label_for_type_ref_target(target_node, source_file)
        if not label:
            continue
        if metadata.get("qualified"):
            resolved = _resolve_qualified(label, metadata.get("ref_qualifier"), source_node, source_file)
        else:
            resolved = _resolve_label(label, source_node, source_file)
        target = edge.get("target")
        desired = resolved or _dangling_stub_id(label, target)
        if desired != target:
            edge["target"] = desired
            if isinstance(target, str) and _is_placeholder(target_node):
                repointed_from.add(target)

    if not repointed_from:
        return

    still_referenced: set[str] = set()
    for edge in all_edges:
        still_referenced.add(edge.get("source"))
        still_referenced.add(edge.get("target"))
    all_nodes[:] = [
        node for node in all_nodes
        if node.get("id") not in repointed_from or node.get("id") in still_referenced
    ]


# ── Recovery from a parse the grammar could not complete ─────────────────────
#
# A C# file must not vanish from the graph because of one construct the grammar
# does not know yet. Measured 2026-09-13 on tree-sitter-c-sharp 0.23.5, which is
# the NEWEST release on PyPI (0.21.2, 0.21.3, 0.23.0, 0.23.1, 0.23.4, 0.23.5), so
# this is not a version to bump past: the SLICE pattern `..` fails to parse.
#
#     r is ["--lang", var c]        parses
#     r is []                       parses
#     r is { Length: 1 }            parses
#     r is [.. var rest]            ERROR
#     r is ["--lang", var c, .. r]  ERROR
#
# One such line, at line 56 of a 229-line file, put an ERROR node over the file
# from line 1 and the whole file contributed a single symbol — its own file node.
# Every type in it, and every edge into those types, was simply absent, and the
# only trace was one warning line.
#
# `extractors/sql.py` has carried a regex recovery for exactly this shape of
# problem since #2180, and `blade.py` is regex from end to end; this is the C#
# one. It recovers TYPE DECLARATIONS only, on purpose: a regex can see that
# `class Foo` is declared and cannot see what `Foo.Bar()` resolves to, and a
# graph is better missing an edge than carrying an invented one.
import re

# Strings and comments are blanked before the scan: `// class Ghost` and
# `"class Ghost"` must not mint a type. Kept as a linear scanner rather than one
# regex because the span kinds interact — a quote inside a comment opens nothing,
# and C# has four string forms with different escapes.
#
#   "…"      ordinary, backslash escapes, ends at end of line if unclosed
#   @"…"     verbatim, spans lines, a doubled "" is one quote
#   """…"""  raw (C# 11), spans lines, any run of 3+ quotes opens and closes
#   '…'      char, backslash escapes
#
# Interpolation (`$"…{Expr}…"`) is blanked whole: a type cannot be declared
# inside an interpolation hole, so nothing recoverable is lost.
def _mask_csharp(source: str) -> str:
    out = list(source)
    n = len(source)
    i = 0

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = source[i]
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif ch == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif ch == '"' and source.startswith('"""', i):
            fence = len(source) - len(source[i:].lstrip('"'))
            quotes = '"' * fence
            j = source.find(quotes, i + fence)
            j = n if j < 0 else j + fence
            blank(i, j)
            i = j
        elif ch == "@" and i + 1 < n and source[i + 1] == '"':
            j = i + 2
            while j < n:
                if source[j] == '"':
                    if j + 1 < n and source[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
        elif ch in '"\'':
            j = i + 1
            while j < n and source[j] != ch and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            j = min(j + 1, n)
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


# `record class X` / `record struct X` name the same declaration twice — once as
# `record`, once as `class` — and both matches carry the same name and line, so
# they mint one id and `add_node` drops the second in silence. Nothing to guard.
_CSHARP_TYPE_RX = re.compile(
    r"\b(?P<kind>class|struct|interface|enum|record)\s+"
    r"(?:(?:class|struct)\s+)?"
    r"(?P<name>[A-Za-z_]\w*)"
)
_CSHARP_NAMESPACE_RX = re.compile(
    r"\bnamespace\s+(?P<name>[A-Za-z_][\w.]*)\s*(?P<open>[;{])"
)


def recover_csharp_types(source: str) -> list[dict]:
    """Type declarations a regex can see, for a file the parser could not finish.

    Returns ``{"name", "namespace", "line", "parent"}`` per declaration, in the
    order they appear. ``namespace`` is the enclosing namespace — file-scoped
    (`namespace X;`) and block (`namespace X { }`) both, nested ones joined with
    dots. ``parent`` is the enclosing TYPE's name, empty at the top level: it is
    both the `is_nested_type` mark and the node to hang the containment edge on,
    and a type id carries no parent name, so the caller can rebuild it.
    """
    masked = _mask_csharp(source)
    starts = [0]
    for index, char in enumerate(masked):
        if char == "\n":
            starts.append(index + 1)

    def line_of(position: int) -> int:
        low, high = 0, len(starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if starts[mid] <= position:
                low = mid
            else:
                high = mid - 1
        return low + 1

    events: list[tuple[int, int, str, object]] = []
    for match in _CSHARP_NAMESPACE_RX.finditer(masked):
        events.append((match.start(), 0, "namespace", match))
    for match in _CSHARP_TYPE_RX.finditer(masked):
        events.append((match.start(), 0, "type", match))
    for index, char in enumerate(masked):
        if char in "{};":
            events.append((index, 1, char, None))
    events.sort(key=lambda event: (event[0], event[1]))

    found: list[dict] = []
    stack: list[tuple[str, str]] = []
    pending: tuple[str, str] | None = None
    file_namespace = ""

    for position, _, kind, match in events:
        if kind == "namespace":
            name = match.group("name")  # type: ignore[union-attr]
            if match.group("open") == ";":  # type: ignore[union-attr]
                file_namespace = name
            else:
                pending = ("namespace", name)
        elif kind == "type":
            name = match.group("name")  # type: ignore[union-attr]
            namespaces = [part for part in (file_namespace,) if part]
            namespaces += [value for frame, value in stack if frame == "namespace"]
            found.append({
                "name": name,
                "namespace": ".".join(namespaces),
                "line": line_of(position),
                "parent": next(
                    (value for frame, value in reversed(stack) if frame == "type"), ""
                ),
            })
            pending = ("type", name)
        elif kind == "{":
            # An unnamed block — a method body, an initializer — is pushed too, so a
            # type declared inside one still counts as nested and `}` stays balanced.
            stack.append(pending or ("block", ""))
            pending = None
        elif kind == "}":
            if stack:
                stack.pop()
        elif kind == ";":
            # `record Point(int X, int Y);` and `enum E;` declare with no body: the
            # declaration is over, so it must not swallow the next block.
            pending = None

    return found
