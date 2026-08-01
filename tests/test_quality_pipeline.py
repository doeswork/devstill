from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devstill.indexer import RepositoryIndexer
from devstill.models import ChunkPolicy
from devstill.parsers import parse_source_file
from devstill.parsers.detection import noisy_reason

from .conftest import FakeChroma, FakeEmbedder


def _searchable(result) -> list:
    return [
        chunk
        for chunk in result.chunks
        if chunk.parser_metadata.get("searchable", True)
    ]


def test_unknown_text_uses_bounded_natural_fallback(tmp_path: Path) -> None:
    path = tmp_path / "notes.unknown"
    path.write_text(
        "# Authentication\n\n"
        "The login endpoint verifies a session.\n\n"
        "# Registration\n\n"
        "The registration endpoint creates an account.\n",
        encoding="utf-8",
    )
    policy = ChunkPolicy(max_chars=65, max_lines=4, overlap_lines=1)

    result = parse_source_file(path, tmp_path, policy=policy)

    searchable = _searchable(result)
    assert result.quality is not None
    assert result.quality.status == "MEH"
    assert "generic text fallback" in result.quality.reasons
    assert result.chunks[0].code == path.read_text(encoding="utf-8")
    assert result.chunks[0].parser_metadata["searchable"] is False
    assert len(searchable) >= 2
    assert all(len(chunk.code) <= 65 for chunk in searchable)
    assert all(chunk.end_line - chunk.start_line + 1 <= 4 for chunk in searchable)


def test_oversized_structural_chunks_keep_parent_and_bounded_segments(
    tmp_path: Path,
) -> None:
    path = tmp_path / "large.py"
    path.write_text(
        "def authenticate():\n"
        + "".join(f"    value_{index} = {index}\n" for index in range(20))
        + "    return True\n",
        encoding="utf-8",
    )
    policy = ChunkPolicy(max_chars=100, max_lines=6, overlap_lines=2)

    result = parse_source_file(path, tmp_path, policy=policy)

    functions = [chunk for chunk in result.chunks if chunk.name == "authenticate"]
    parent = next(
        chunk
        for chunk in functions
        if not chunk.parent_id.endswith("authenticate::function")
    )
    segments = [
        chunk
        for chunk in functions
        if chunk.parser_metadata.get("segmented_from") == parent.id
    ]
    assert parent.parser_metadata["searchable"] is False
    assert parent.code == path.read_text(encoding="utf-8").rstrip("\n")
    assert segments
    assert all(len(chunk.code) <= 100 for chunk in segments)
    assert all(chunk.end_line - chunk.start_line + 1 <= 6 for chunk in segments)
    assert all(chunk.name == "authenticate" for chunk in segments)
    assert result.quality is not None
    assert result.quality.status == "MEH"


def test_unsegmentable_giant_line_is_bad(tmp_path: Path) -> None:
    path = tmp_path / "data.custom"
    path.write_text("x" * 101, encoding="utf-8")

    result = parse_source_file(
        path,
        tmp_path,
        policy=ChunkPolicy(max_chars=100, max_lines=5, overlap_lines=1),
    )

    assert result.errors
    assert result.quality is not None
    assert result.quality.status == "BAD"
    assert not _searchable(result)
    assert result.chunks[0].code == "x" * 101


def test_forced_fallback_windows_keep_configured_line_overlap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dense.custom"
    path.write_text(
        "".join(f"line {index}\n" for index in range(10)),
        encoding="utf-8",
    )

    result = parse_source_file(
        path,
        tmp_path,
        policy=ChunkPolicy(max_chars=1000, max_lines=4, overlap_lines=2),
    )

    ranges = [(chunk.start_line, chunk.end_line) for chunk in _searchable(result)]
    assert ranges == [(1, 4), (3, 6), (5, 8), (7, 10)]
    assert result.quality is not None
    assert result.quality.fallback_segments == 4


def test_noisy_and_binary_detection(tmp_path: Path) -> None:
    lockfile = tmp_path / "package-lock.json"
    lockfile.write_text('{"lockfileVersion": 3}', encoding="utf-8")
    skipped = parse_source_file(lockfile, tmp_path)
    assert skipped.skipped
    assert skipped.quality is not None
    assert skipped.quality.status == "SKIPPED"
    assert skipped.quality.disposition == "ignored"
    assert skipped.quality.noisy
    assert not skipped.chunks

    indexed = parse_source_file(lockfile, tmp_path, index_noisy_files=True)
    assert indexed.quality is not None
    assert indexed.quality.status == "MEH"
    assert indexed.quality.noisy
    assert _searchable(indexed)

    binary = tmp_path / "image.bin"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\0payload")
    result = parse_source_file(binary, tmp_path)
    assert result.binary
    assert result.skipped
    assert result.quality is not None
    assert result.quality.status == "SKIPPED"
    assert result.quality.category == "binary"


def test_all_noisy_file_categories_are_detected(tmp_path: Path) -> None:
    cases = [
        (tmp_path / "yarn.lock", "yarn.lock", b"lock", "lockfile"),
        (tmp_path / "app.js.map", "app.js.map", b"{}", "source map"),
        (tmp_path / "app.min.js", "app.min.js", b"x=1", "minified bundle"),
        (
            tmp_path / "vendor" / "library.rb",
            "vendor/library.rb",
            b"puts :ok",
            "vendored/generated directory",
        ),
        (
            tmp_path / "generated.go",
            "generated.go",
            b"// Code generated; DO NOT EDIT.\npackage generated\n",
            "generated-do-not-edit header",
        ),
    ]
    for path, relative, raw, expected in cases:
        assert expected in (noisy_reason(path, relative, raw) or "")


def test_noisy_native_source_uses_bounded_fallback_when_enabled(
    tmp_path: Path,
) -> None:
    path = tmp_path / "generated.go"
    path.write_text(
        "// Code generated; DO NOT EDIT.\npackage generated\nfunc Run() {}\n",
        encoding="utf-8",
    )

    result = parse_source_file(path, tmp_path, index_noisy_files=True)

    assert result.parser == "plain_text"
    assert result.quality is not None
    assert result.quality.status == "MEH"
    assert result.quality.noisy
    assert all(chunk.kind != "function" for chunk in result.chunks)


def test_index_quality_persists_and_unchanged_run_reuses_it(
    configured_repo: tuple,
    capsys,
) -> None:
    repo, config_path, config = configured_repo
    config = config.model_copy(
        update={
            "files": config.files.model_copy(
                update={
                    "scan_all_text": True,
                    "exclude": [
                        *config.files.exclude,
                        "devstill.json",
                    ],
                }
            )
        }
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "good.py").write_text("def login():\n    return True\n", encoding="utf-8")
    (repo / "notes.custom").write_text("# Login\n\nSession notes.\n", encoding="utf-8")
    (repo / "package-lock.json").write_text("{}", encoding="utf-8")
    (repo / "image.bin").write_bytes(b"\0binary")
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "add",
            "good.py",
            "notes.custom",
            "package-lock.json",
            "image.bin",
        ],
        check=True,
    )
    indexer = RepositoryIndexer(
        config_path,
        config,
        embedder=FakeEmbedder(),
        chroma_store=FakeChroma(),
    )

    first = indexer.run(verbose=True)
    diagnostics = capsys.readouterr().err
    second = indexer.run()

    assert first.good_files == 1
    assert first.meh_files == 1
    assert first.bad_files == 0
    assert first.ignored_files == 1
    assert first.binary_files_skipped == 1
    assert "GOOD files by category/parser:" in diagnostics
    assert "code / python_ast: 1" in diagnostics
    assert "MEH notes.custom: generic text fallback" in diagnostics
    assert "SKIPPED package-lock.json (generated): lockfile" in diagnostics
    assert second.unchanged_files == 4
    assert second.parsed_files == 0
    assert second.search_units_embedded == 0
    assert (second.good_files, second.meh_files, second.bad_files) == (1, 1, 0)
    report = json.loads(
        (repo / ".devstill/manifests/quality.json").read_text(encoding="utf-8")
    )
    assert report["binary_files_skipped"] == 1
    assert report["binary_files"] == ["image.bin"]
    assert {item["path"] for item in report["skipped"]} == {"image.bin"}
    assert {item["path"] for item in report["files"]} == {
        "good.py",
        "notes.custom",
        "package-lock.json",
        "image.bin",
    }
    assert len(indexer.store.list_quality()) == 4
    indexer.close()
