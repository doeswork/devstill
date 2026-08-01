from __future__ import annotations

from pathlib import Path

import pytest

from devstill.models import ChunkPolicy
from devstill.parsers import parse_source_file


def _searchable(result):
    return [
        chunk
        for chunk in result.chunks
        if chunk.parser_metadata.get("searchable", True)
    ]


def test_markdown_heading_hierarchy_preserves_fenced_code(tmp_path: Path) -> None:
    path = tmp_path / "README.md"
    source = (
        "Intro text.\n\n"
        "# Authentication\n\n"
        "Login overview.\n\n"
        "```markdown\n"
        "# Not a heading\n"
        "```\n\n"
        "## Sessions\n\n"
        "Cookie details.\n"
    )
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)
    sections = _searchable(result)

    assert result.quality is not None
    assert result.quality.status == "GOOD"
    assert result.quality.category == "documentation"
    assert [chunk.name for chunk in sections] == [
        "preamble",
        "Authentication",
        "Sessions",
    ]
    assert sections[2].qualname == "Authentication / Sessions"
    assert "# Not a heading" in sections[1].code
    assert "## Sessions" not in sections[1].code
    assert result.chunks[0].code == source


def test_asciidoc_heading_hierarchy_has_exact_sections(tmp_path: Path) -> None:
    path = tmp_path / "guide.adoc"
    source = "= Guide\n\nIntro.\n\n== Login\n\nAuthenticate.\n"
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)
    sections = _searchable(result)

    assert [chunk.name for chunk in sections] == ["Guide", "Login"]
    assert sections[1].qualname == "Guide / Login"
    assert sections[0].code == "= Guide\n\nIntro.\n\n"
    assert sections[1].code == "== Login\n\nAuthenticate.\n"


@pytest.mark.parametrize(
    ("filename", "source", "names"),
    [
        (
            "package.json",
            '{\n  "name": "app",\n  "scripts": {\n    "test": "pytest"\n  }\n}\n',
            ["name", "scripts"],
        ),
        (
            "settings.yaml",
            "server:\n  port: 8000\ndatabase:\n  url: sqlite\n",
            ["server", "database"],
        ),
        (
            "pyproject.toml",
            "name = 'app'\n\n[tool.ruff]\nline-length = 88\n",
            ["preamble", "tool.ruff"],
        ),
        (
            "app.service",
            "[Unit]\nDescription=App\n\n[Service]\nExecStart=/app\n",
            ["Unit", "Service"],
        ),
        (
            "Dockerfile",
            "FROM python:3.12\nWORKDIR /app\nCOPY . .\n",
            ["FROM@1", "WORKDIR@2", "COPY@3"],
        ),
        (
            "Makefile",
            "install:\n\tpip install -e .\n\ntest:\n\tpytest\n",
            ["install", "test"],
        ),
    ],
)
def test_configuration_formats_have_exact_logical_sections(
    tmp_path: Path,
    filename: str,
    source: str,
    names: list[str],
) -> None:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)
    chunks = _searchable(result)

    assert result.quality is not None
    assert result.quality.status == "GOOD"
    assert result.quality.category == "configuration"
    assert [chunk.name for chunk in chunks] == names
    assert result.chunks[0].code == source
    source_lines = source.splitlines(keepends=True)
    assert all(
        chunk.code == "".join(source_lines[chunk.start_line - 1 : chunk.end_line])
        for chunk in chunks
    )


@pytest.mark.parametrize(
    "filename",
    [
        "notes.txt",
        "guide.rst",
        "manual.adoc",
        "requirements-dev.txt",
        ".gitignore",
        ".env.example",
        "nginx.conf",
    ],
)
def test_known_text_names_are_not_generic_fallback(
    tmp_path: Path, filename: str
) -> None:
    path = tmp_path / filename
    path.write_text("Authentication\n\nSession configuration.\n", encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert result.quality is not None
    assert result.quality.status == "GOOD"
    assert result.quality.category in {"documentation", "configuration"}
    assert "generic text fallback" not in result.quality.reasons


def test_invalid_json_is_bad_without_questionable_chunks(tmp_path: Path) -> None:
    path = tmp_path / "package.json"
    path.write_text('{"name": }\n', encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert result.errors
    assert result.chunks == []
    assert result.quality is not None
    assert result.quality.status == "BAD"
    assert result.quality.category == "configuration"


def test_json_with_jsonc_comments_is_valid_configuration(tmp_path: Path) -> None:
    path = tmp_path / "tsconfig.app.json"
    source = (
        "{\n"
        '  "compilerOptions": {\n'
        "    /* Bundler mode */\n"
        '    "module": "esnext",\n'
        "  },\n"
        '  "include": ["src"]\n'
        "}\n"
    )
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert result.errors == []
    assert result.quality is not None
    assert result.quality.status == "GOOD"
    assert [chunk.name for chunk in _searchable(result)] == [
        "compilerOptions",
        "include",
    ]


def test_invalid_jsonc_is_bad(tmp_path: Path) -> None:
    path = tmp_path / "settings.jsonc"
    path.write_text('{\n  // comment\n  "name":,\n}\n', encoding="utf-8")

    result = parse_source_file(path, tmp_path)

    assert result.quality is not None
    assert result.quality.status == "BAD"
    assert result.errors


def test_natural_document_segmentation_stays_good_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    source = "# Guide\n\n" + "\n\n".join(
        f"Paragraph {index} contains useful authentication notes."
        for index in range(12)
    )
    path.write_text(source, encoding="utf-8")

    result = parse_source_file(
        path,
        tmp_path,
        policy=ChunkPolicy(max_chars=150, max_lines=8, overlap_lines=2),
    )
    chunks = _searchable(result)

    assert result.quality is not None
    assert result.quality.status == "GOOD"
    assert len(chunks) > 1
    assert all(len(chunk.code) <= 150 for chunk in chunks)
    assert all(chunk.end_line - chunk.start_line + 1 <= 8 for chunk in chunks)


def test_unsegmentable_known_text_line_is_bad(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("x" * 101, encoding="utf-8")

    result = parse_source_file(
        path,
        tmp_path,
        policy=ChunkPolicy(max_chars=100, max_lines=5, overlap_lines=1),
    )

    assert result.quality is not None
    assert result.quality.status == "BAD"
    assert result.errors
    assert not _searchable(result)
