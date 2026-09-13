"""The AST cache must notice an extractor that changed without a version bump.

`cache/ast/v{version}-s{schema}/` rests on «a version means an extractor». That holds
for an installed wheel and fails for a source checkout, which is how this repo is
developed (`uv tool install --editable`): the extractor changes many times under one
version that never moves, so every entry written by yesterday's extractor is served to
today's.

Measured 2026-09-13: a fix to the C# extractor landed, the graph did not move, and the
conclusion «the fix did not work» was wrong — every unchanged source file was answered
from cache. `update --force` does not help; its `--force` is the node-count guard, not
the cache. Only deleting `cache/ast/` by hand did, and nothing said so.

So in a checkout the version segment carries a fingerprint of the extractor sources.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from graphify.cache import _extractor_fingerprint, cache_dir


def _package(tmp_path: Path, *, git: bool, extractor_body: str = "x = 1\n") -> Path:
    """A stand-in package directory: ``<root>/graphify/`` with or without a sibling .git."""
    root = tmp_path / "checkout"
    package = root / "graphify"
    (package / "extractors").mkdir(parents=True)
    (package / "extract.py").write_text("# extract\n", encoding="utf-8")
    (package / "extractors" / "csharp.py").write_text(extractor_body, encoding="utf-8")
    if git:
        (root / ".git").mkdir()
    return package


def test_a_released_install_carries_no_fingerprint(tmp_path: Path) -> None:
    """No .git beside the package: the files cannot change under the version, so the
    version alone is the truth and the segment stays exactly as it was."""
    assert _extractor_fingerprint(_package(tmp_path, git=False)) == ""


def test_a_checkout_carries_one(tmp_path: Path) -> None:
    fingerprint = _extractor_fingerprint(_package(tmp_path, git=True))
    assert fingerprint.startswith("+")
    assert len(fingerprint) == 13


def test_it_moves_when_an_extractor_moves(tmp_path: Path) -> None:
    before = _extractor_fingerprint(_package(tmp_path / "a", git=True, extractor_body="x = 1\n"))
    after = _extractor_fingerprint(_package(tmp_path / "b", git=True, extractor_body="x = 2\n"))
    assert before != after


def test_it_is_content_not_mtime(tmp_path: Path) -> None:
    """A branch switch rewrites mtimes without changing code; an identical tree must hit."""
    package = _package(tmp_path, git=True)
    first = _extractor_fingerprint(package)
    source = package / "extractors" / "csharp.py"
    body = source.read_bytes()
    source.write_bytes(b"# rewritten, same length of nothing\n")
    source.write_bytes(body)
    assert _extractor_fingerprint(package) == first


def test_a_new_extractor_file_moves_it(tmp_path: Path) -> None:
    """`extractors/*.py` is globbed, so adding a language changes the fingerprint."""
    package = _package(tmp_path, git=True)
    before = _extractor_fingerprint(package)
    (package / "extractors" / "brandnew.py").write_text("y = 1\n", encoding="utf-8")
    assert _extractor_fingerprint(package) != before


def test_the_digest_is_over_the_sources_in_a_fixed_order(tmp_path: Path) -> None:
    """extract.py first, then extractors/*.py sorted — so two machines agree."""
    package = _package(tmp_path, git=True)
    digest = hashlib.sha256()
    digest.update((package / "extract.py").read_bytes())
    for source in sorted((package / "extractors").glob("*.py")):
        digest.update(source.read_bytes())
    assert _extractor_fingerprint(package) == "+" + digest.hexdigest()[:12]


def test_the_cache_directory_carries_it_and_sweeps_the_previous_one(tmp_path: Path) -> None:
    """`_cleanup_stale_ast_entries` already sweeps sibling `v*/`, so a fingerprint that
    moves retires the old directory instead of piling up beside it."""
    # The stale directory must exist BEFORE the first call: the sweep runs once per
    # process per (base, version), which in real use is the first call made under a
    # fingerprint that has just moved.
    stale = tmp_path / "graphify-out" / "cache" / "ast" / "v0.0.0+000000000000-s2"
    stale.mkdir(parents=True)
    (stale / "cafebabe.json").write_text("{}", encoding="utf-8")

    directory = cache_dir(tmp_path, "ast")

    assert _extractor_fingerprint() in directory.name
    assert not stale.exists()
    assert cache_dir(tmp_path, "ast") == directory
