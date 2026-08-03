from __future__ import annotations

from pathlib import Path

import pytest

from devstill.parsers import (
    FALLBACK_PARSER_VERSION,
    JAVASCRIPT_FAMILY_PARSER_VERSION,
    PARSER_VERSION,
    parse_source_file,
    parser_version_for,
)
from devstill.parsers.base import MARKUP_TEXT_PARSER_VERSION
from devstill.parsers.javascript_tree_sitter import parse_javascript_family


def test_tsx_boundaries_exports_nesting_and_named_callables(
    tmp_path: Path,
) -> None:
    source = """import React, { useCallback, useEffect } from 'react'
import type { FormEvent } from 'react'

type SessionProps = {
  user: string
}

interface ApiResult {
  ok: boolean
}

enum Status {
  Ready,
}

// Handles account registration.
export function RegisterPage({ user }: SessionProps) {
  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    return 'héllo'
  }

  const loadPosts = useCallback(async () => {
    return user
  }, [user])

  useEffect(() => {
    function hiddenInsideAnonymousCallback() {
      return user
    }
    hiddenInsideAnonymousCallback()
  }, [user])

  return <button onClick={() => handleSubmit}>Register</button>
}

export const Card = React.memo(() => <article />)

class SessionService {
  @trace
  async refresh() {
    return true
  }
}

function Neighbor() {
  return null
}
"""
    path = tmp_path / "App.tsx"
    path.write_text(source, encoding="utf-8")

    result = parse_javascript_family(path, tmp_path)
    assert not result.errors
    by_name = {chunk.qualname: chunk for chunk in result.chunks if chunk.qualname}

    module = by_name["App"]
    imports = by_name["imports@1"]
    register = by_name["RegisterPage"]
    submit = by_name["RegisterPage.handleSubmit"]
    load_posts = by_name["RegisterPage.loadPosts"]
    card = by_name["Card"]
    service = by_name["SessionService"]
    refresh = by_name["SessionService.refresh"]
    neighbor = by_name["Neighbor"]

    assert module.kind == "module"
    assert module.parser_metadata["searchable"] is False
    assert imports.kind == "import_group"
    assert imports.code == "\n".join(source.splitlines()[:2])
    assert by_name["SessionProps"].kind == "type_alias"
    assert by_name["ApiResult"].kind == "interface"
    assert by_name["Status"].kind == "enum"

    assert register.kind == "function"
    assert register.leading_comments == "// Handles account registration."
    assert register.code.startswith("export function RegisterPage")
    assert "héllo" in register.code
    assert "function Neighbor" not in register.code
    assert register.parser_metadata["exported"] is True
    assert submit.kind == "nested_function"
    assert submit.parent_id == register.id
    assert submit.parser_metadata["async"] is True
    assert load_posts.kind == "nested_function"
    assert load_posts.parent_id == register.id
    assert load_posts.parser_metadata["wrapper"] == "useCallback"
    assert "hiddenInsideAnonymousCallback" not in by_name

    assert card.kind == "function"
    assert card.code.startswith("export const Card")
    assert card.parser_metadata["wrapper"] == "React.memo"
    assert service.kind == "class"
    assert refresh.kind == "async_method"
    assert refresh.parent_id == service.id
    assert refresh.code.startswith("@trace\n  async refresh")
    assert refresh.parser_metadata["decorators"] == ["@trace"]
    assert neighbor.code == "function Neighbor() {\n  return null\n}"


@pytest.mark.parametrize(
    ("filename", "source", "expected"),
    [
        ("module.js", "export function run() { return 1 }\n", "run"),
        ("view.jsx", "export const View = () => <div />\n", "View"),
        (
            "types.ts",
            "export interface Result { ok: boolean }\n",
            "Result",
        ),
        (
            "view.tsx",
            "export function View() { return <main /> }\n",
            "View",
        ),
    ],
)
def test_javascript_family_routing(
    tmp_path: Path, filename: str, source: str, expected: str
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert not result.errors
    assert any(chunk.name == expected for chunk in result.chunks)
    assert all(
        chunk.parser_metadata["parser"] == "tree_sitter" for chunk in result.chunks
    )


def test_javascript_syntax_and_encoding_errors(tmp_path: Path) -> None:
    broken = tmp_path / "broken.tsx"
    broken.write_text("function broken( { return <div> }\n", encoding="utf-8")
    result = parse_javascript_family(broken, tmp_path)
    assert not result.chunks
    assert result.errors
    assert result.errors[0].line == 1

    invalid = tmp_path / "invalid.ts"
    invalid.write_bytes(b"export const value = '\xff'\n")
    result = parse_javascript_family(invalid, tmp_path)
    assert result.skipped
    assert not result.chunks
    assert "UTF-8" in result.errors[0].message


@pytest.mark.parametrize("label", ["Save & Send", "Privacy & Safety"])
def test_tsx_visible_ampersand_recovers_to_exact_bounded_fallback(
    tmp_path: Path, label: str
) -> None:
    path = tmp_path / "Page.tsx"
    source = f"export function Page() {{ return <button>{label}</button> }}\n"
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert result.errors
    assert result.quality is not None
    assert result.quality.status == "MEH"
    assert path.read_text(encoding="utf-8") == source
    assert result.chunks[0].code == source
    searchable = [
        chunk
        for chunk in result.chunks
        if chunk.parser_metadata.get("searchable", True)
    ]
    assert [chunk.code for chunk in searchable] == [source]
    assert searchable[0].parser_metadata["structural_fallback"] == (
        "javascript_tree_sitter"
    )


def test_entrypoint_and_default_export_expressions_are_searchable(
    tmp_path: Path,
) -> None:
    entrypoint = tmp_path / "main.tsx"
    entrypoint.write_text("createRoot(root).render(<App />)\n", encoding="utf-8")
    entry_chunks = parse_javascript_family(entrypoint, tmp_path).chunks
    expression = next(chunk for chunk in entry_chunks if chunk.kind == "expression")
    assert expression.name == "expression@1"
    assert expression.code == "createRoot(root).render(<App />)"

    config = tmp_path / "vite.config.ts"
    config.write_text(
        "export default defineConfig({ plugins: [] })\n", encoding="utf-8"
    )
    config_chunks = parse_javascript_family(config, tmp_path).chunks
    default_export = next(
        chunk for chunk in config_chunks if chunk.name == "default_export"
    )
    assert default_export.kind == "assignment"
    assert default_export.parser_metadata["default_export"] is True
    assert default_export.code.startswith("export default defineConfig")

    commonjs = tmp_path / "config.js"
    commonjs.write_text("module.exports = { enabled: true }\n", encoding="utf-8")
    commonjs_chunks = parse_javascript_family(commonjs, tmp_path).chunks
    assignment = next(
        chunk for chunk in commonjs_chunks if chunk.name == "module.exports"
    )
    assert assignment.kind == "assignment"
    assert assignment.parser_metadata["targets"] == ["module.exports"]


def test_parser_versions_only_migrate_javascript_family(tmp_path: Path) -> None:
    assert parser_version_for(tmp_path / "module.py") == PARSER_VERSION
    assert parser_version_for(tmp_path / "notes.md") == MARKUP_TEXT_PARSER_VERSION
    assert parser_version_for(tmp_path / "notes.unknown") == FALLBACK_PARSER_VERSION
    for suffix in (".js", ".jsx", ".ts", ".tsx"):
        assert (
            parser_version_for(tmp_path / f"module{suffix}")
            == JAVASCRIPT_FAMILY_PARSER_VERSION
        )
