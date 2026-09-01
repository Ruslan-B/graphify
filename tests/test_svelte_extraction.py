"""Tests for `.svelte` symbol extraction.

Svelte files are a `<script>` block plus template markup, the same container
shape Vue SFCs have. Feeding the whole file to the JS grammar produces a
top-level ERROR node because the template is not valid JS, so before masking
only imports were rescued by regex and every declaration in the script was lost.
`_vue_mask_non_script` is not Vue-specific — its regex matches any
`<script ...>...</script>` — so the same masking serves here.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_svelte


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_svelte_script_symbols_are_extracted_with_correct_lines(tmp_path):
    comp = _write(
        tmp_path / "Widget.svelte",
        """<script lang="ts">
import { onMount } from 'svelte'

interface Props { id: number }

const ready = onMount(() => {})

function toggle(): void {
}
</script>

<button on:click={toggle}>x</button>
""",
    )
    result = extract_svelte(comp)
    by_label = {n["label"]: n for n in result["nodes"]}
    assert "Props" in by_label
    assert "ready" in by_label
    assert "toggle()" in by_label
    # Masking must preserve line numbers against the original .svelte file.
    assert by_label["Props"]["source_location"] == "L4"
    assert by_label["ready"]["source_location"] == "L6"
    assert by_label["toggle()"]["source_location"] == "L8"
