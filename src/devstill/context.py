from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import TypedDict

from devstill.hashing import hash_text
from devstill.models import SearchResult, SearchUnit, SourceChunk


class ContextRecord(TypedDict):
    path: str
    lines: list[int]
    symbol: str
    kind: str
    code: str


def build_search_unit(chunk: SourceChunk) -> SearchUnit:
    lines = [
        f"Repository path: {chunk.path}",
        f"Language: {chunk.language}",
        f"Symbol kind: {chunk.kind}",
    ]
    if chunk.qualname:
        lines.append(f"Qualified symbol: {chunk.qualname}")
    elif chunk.name:
        lines.append(f"Symbol: {chunk.name}")
    targets = chunk.parser_metadata.get("targets")
    if targets:
        lines.append(f"Parser targets: {', '.join(str(value) for value in targets)}")
    bases = chunk.parser_metadata.get("bases")
    if bases:
        lines.append(f"Base classes: {', '.join(str(value) for value in bases)}")
    lines.extend(["", "Source:", chunk.code])
    embedding_text = "\n".join(lines)
    return SearchUnit(
        id=f"search::{chunk.id}",
        source_chunk_ids=[chunk.id],
        embedding_text=embedding_text,
        summary=None,
        concepts=[],
        content_hash=hash_text(embedding_text),
    )


def is_test_chunk(chunk: SourceChunk) -> bool:
    path = PurePosixPath(chunk.path)
    parts = [part.casefold() for part in path.parts]
    stem = path.stem.casefold()
    if any(part in {"test", "tests", "__tests__"} for part in parts[:-1]):
        return True
    if (
        stem in {"test", "tests"}
        or stem.startswith(("test_", "spec_"))
        or stem.endswith(("_test", "_spec", ".test", ".spec"))
    ):
        return True
    symbol_parts = (chunk.qualname or chunk.name or "").split(".")
    return any(
        part.casefold() in {"test", "tests"}
        or part.casefold().startswith("test_")
        or part.endswith(("Test", "Tests"))
        for part in symbol_parts
    )


def select_context_results(
    results: list[SearchResult],
    limit: int,
    include_tests: bool,
) -> list[SearchResult]:
    selected = (
        results
        if include_tests
        else [result for result in results if not is_test_chunk(result.chunk)]
    )
    return selected[:limit]


def context_record(result: SearchResult) -> ContextRecord:
    chunk = result.chunk
    return {
        "path": chunk.path,
        "lines": [chunk.start_line, chunk.end_line],
        "symbol": chunk.qualname or chunk.name or chunk.path,
        "kind": chunk.kind,
        "code": chunk.code,
    }


def render_context_json(results: list[SearchResult]) -> str:
    return json.dumps([context_record(result) for result in results], indent=2)


_LANGUAGE_FENCES = {
    "js": "javascript",
    "md": "markdown",
    "py": "python",
    "ts": "typescript",
}


def _safe_fence(code: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", code)), default=0
    )
    return "`" * max(3, longest + 1)


def render_context_markdown(
    results: list[SearchResult],
    query: str,
    repository: str,
) -> str:
    lines = [
        "# Devstill AI Context",
        "",
        f"Query: {query}",
        f"Repository: {repository}",
        "",
    ]
    if not results:
        lines.extend(["No relevant context found.", ""])
        return "\n".join(lines)
    for index, result in enumerate(results, start=1):
        chunk = result.chunk
        symbol = chunk.qualname or chunk.name or chunk.path
        language = _LANGUAGE_FENCES.get(chunk.language, chunk.language)
        fence = _safe_fence(chunk.code)
        lines.extend(
            [
                (
                    f"## {index}. {chunk.path}:{chunk.start_line}-{chunk.end_line} "
                    f"— {symbol} ({chunk.kind})"
                ),
                "",
                f"{fence}{language}",
                chunk.code,
                fence,
                "",
            ]
        )
    return "\n".join(lines)


def write_context_artifacts(
    directory: Path,
    markdown: str,
    json_output: str,
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / ".context.md"
    json_path = directory / ".context.json"
    markdown_path.write_text(markdown.rstrip("\n") + "\n", encoding="utf-8")
    json_path.write_text(json_output.rstrip("\n") + "\n", encoding="utf-8")
    return markdown_path, json_path
