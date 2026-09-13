"""C# types recovered from a file the grammar could not finish.

tree-sitter-c-sharp 0.23.5 — the NEWEST release on PyPI as of 2026-09-13, so not a
version to bump past — cannot parse the slice pattern ``..``::

    r is ["--lang", var c]          parses
    r is []                         parses
    r is { Length: 1 }              parses
    r is [.. var rest]              ERROR
    r is ["--lang", var c, .. var r] ERROR

One such line, at line 56 of a 229-line file, put an ERROR node over the file from
line 1: the whole file contributed a single symbol, its own file node, and every
type in it left the graph with one warning line as the only trace.

``recover_csharp_types`` reads the declarations out of the masked source and
``_extract_generic`` adds whatever the walk did not reach. Types only, on purpose:
a regex can see that ``class Foo`` is declared and cannot see what a call inside it
resolves to, and a graph is better missing an edge than carrying an invented one.

Validated against the real walk on a 60-file C# repo: of 59 files the parser could
finish, the recovery named exactly the same types with the same nesting — no misses,
no phantoms — and it differed only on the file the parser had failed.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_csharp
from graphify.extractors.csharp import recover_csharp_types

SLICE_PATTERN = """\
namespace Kazan.Cli;

internal sealed class CommandLine
{
    public static int Run(string[] rest)
    {
        if (rest is ["--lang", var code, .. var afterLang])
        {
            return code.Length + afterLang.Length;
        }
        return 0;
    }
}
"""


def _types(source: str) -> set[tuple[str, str, str]]:
    return {(d["name"], d["namespace"], d["parent"]) for d in recover_csharp_types(source)}


def test_the_grammar_still_cannot_parse_a_slice_pattern() -> None:
    """The premise of this whole file. When it fails, the recovery may be retired."""
    import tree_sitter_c_sharp
    from tree_sitter import Language, Parser

    parser = Parser(Language(tree_sitter_c_sharp.language()))
    assert parser.parse(SLICE_PATTERN.encode()).root_node.has_error
    assert not parser.parse(b"class A { void M(string[] r) { if (r is [\"x\", var c]) { } } }").root_node.has_error


def test_a_type_survives_a_file_the_parser_could_not_finish(tmp_path: Path) -> None:
    path = tmp_path / "CommandLine.cs"
    path.write_text(SLICE_PATTERN, encoding="utf-8")

    result = extract_csharp(path)

    # That the parse failed at all is the point; WHERE tree-sitter starts the ERROR
    # region is its own business and moves with the file — line 3 here, line 1 in the
    # 229-line original.
    assert result["parse_errors"]
    recovered = [n for n in result["nodes"] if n["label"] == "CommandLine"]
    assert len(recovered) == 1
    node = recovered[0]
    assert node["_callable_class"] is True
    assert node["metadata"]["namespace"] == "Kazan.Cli"
    assert node["source_location"] == "L3"
    # Containment keeps the tree connected: the file owns a top-level type.
    file_ids = {n["id"] for n in result["nodes"] if n["label"] == "CommandLine.cs"}
    assert any(
        e["source"] in file_ids and e["target"] == node["id"] and e["relation"] == "contains"
        for e in result["edges"]
    )


def test_a_file_the_parser_finishes_is_untouched(tmp_path: Path) -> None:
    """The recovery only ever adds, and `add_node` dedupes by id — no doubled types."""
    path = tmp_path / "Clean.cs"
    path.write_text("namespace N;\n\npublic sealed class Clean\n{\n    public int M() => 1;\n}\n", encoding="utf-8")

    result = extract_csharp(path)

    assert "parse_errors" not in result
    assert len([n for n in result["nodes"] if n["label"] == "Clean"]) == 1


def test_a_name_in_a_comment_or_a_string_is_not_a_type() -> None:
    """Masking, or every `// class Ghost` in the corpus mints one."""
    assert _types("// class LineGhost\nclass Real { }") == {("Real", "", "")}
    assert _types("/* class BlockGhost */\nclass Real { }") == {("Real", "", "")}
    assert _types('class Real { string s = "class StringGhost"; }') == {("Real", "", "")}
    assert _types('class Real { string s = @"class VerbatimGhost"; }') == {("Real", "", "")}
    assert _types('class Real { string s = """\nclass RawGhost\n"""; }') == {("Real", "", "")}


def test_a_nested_type_names_the_type_it_sits_in() -> None:
    source = "namespace N;\nclass Outer\n{\n    private sealed class Inner { }\n}\nclass Beside { }\n"
    assert _types(source) == {("Outer", "N", ""), ("Inner", "N", "Outer"), ("Beside", "N", "")}


def test_a_type_declared_inside_a_method_body_is_still_nested() -> None:
    """A brace is pushed for every block, not only for a type, or `}` goes out of step."""
    source = "class Outer\n{\n    void M()\n    {\n        if (true) { }\n    }\n    class Inner { }\n}\n"
    assert _types(source) == {("Outer", "", ""), ("Inner", "", "Outer")}


def test_a_block_namespace_and_a_nested_one() -> None:
    source = "namespace A\n{\n    namespace B\n    {\n        class Deep { }\n    }\n    class Shallow { }\n}\n"
    assert _types(source) == {("Deep", "A.B", ""), ("Shallow", "A", "")}


def test_every_kind_of_type_declaration() -> None:
    source = (
        "namespace N;\n"
        "class C { }\n"
        "struct S { }\n"
        "interface I { }\n"
        "enum E { One }\n"
        "record R(int X);\n"
        "record class RC { }\n"
        "record struct RS { }\n"
    )
    names = {name for name, _, _ in _types(source)}
    assert names == {"C", "S", "I", "E", "R", "RC", "RS"}


def test_a_bodyless_declaration_does_not_swallow_the_next_block() -> None:
    """`record R(int X);` ends at the semicolon — the block after it is not its body."""
    source = "namespace N;\nrecord R(int X);\nclass After\n{\n    class Inside { }\n}\n"
    assert _types(source) == {("R", "N", ""), ("After", "N", ""), ("Inside", "N", "After")}


def test_a_recovered_type_says_so(tmp_path: Path) -> None:
    """Its edges were never extracted. A reader that cannot tell «no edges» from «edges
    unknown» draws the biggest class in a project as an isolated box."""
    path = tmp_path / "CommandLine.cs"
    path.write_text(SLICE_PATTERN, encoding="utf-8")

    result = extract_csharp(path)

    recovered = next(n for n in result["nodes"] if n["label"] == "CommandLine")
    assert recovered["metadata"]["recovered"] is True


def test_a_type_the_walk_reached_does_not(tmp_path: Path) -> None:
    path = tmp_path / "Clean.cs"
    path.write_text("namespace N;\n\npublic sealed class Clean\n{\n    public int M() => 1;\n}\n", encoding="utf-8")

    result = extract_csharp(path)

    clean = next(n for n in result["nodes"] if n["label"] == "Clean")
    assert "recovered" not in (clean.get("metadata") or {})
