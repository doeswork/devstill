from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import TypedDict

from devstill.hashing import hash_text
from devstill.models import SearchResult, SearchUnit, SourceChunk

RETRIEVAL_SCORE_CHANNELS = frozenset(
    {"exact", "prefix", "path", "fuzzy", "lexical", "semantic"}
)
DIRECT_MATCH_CHANNELS = frozenset({"exact", "prefix", "path"})
PLAN_MIN_CHUNKS = 3
PLAN_MAX_CHUNKS = 5
DEEP_PLAN_MIN_CHUNKS = 5
DEEP_PLAN_MAX_CHUNKS = 8


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


def _channel_scores(result: SearchResult) -> dict[str, float]:
    return {
        name: value
        for name, value in result.scores.items()
        if name in RETRIEVAL_SCORE_CHANNELS
    }


def context_candidate_supported(
    selected: list[SearchResult],
    candidate: SearchResult,
) -> bool:
    """Return whether a candidate remains competitive with selected context."""
    scores = _channel_scores(candidate)
    if not selected or DIRECT_MATCH_CHANNELS.intersection(scores):
        return True
    if len(scores) >= 2:
        return True

    tolerances = {"semantic": 0.10, "lexical": 0.15, "fuzzy": 0.15}
    for channel, tolerance in tolerances.items():
        value = scores.get(channel)
        if value is None:
            continue
        references = [
            result.scores[channel] for result in selected if channel in result.scores
        ]
        if references and value >= max(references) - tolerance:
            return True

    if scores:
        return False
    best_final = max((result.final_score for result in selected), default=0.0)
    return best_final > 0.0 and candidate.final_score >= best_final * 0.75


def select_adaptive_context_results(
    results: list[SearchResult],
    *,
    min_limit: int,
    max_limit: int,
    include_tests: bool,
    max_per_file: int = 2,
) -> list[SearchResult]:
    """Select compact, diverse context and stop after a clear relevance drop."""
    if min_limit < 0:
        raise ValueError("minimum context limit must not be negative")
    if max_limit < min_limit:
        raise ValueError("maximum context limit must be at least the minimum")
    if max_per_file < 1:
        raise ValueError("max_per_file must be at least 1")

    filtered = (
        results
        if include_tests
        else [result for result in results if not is_test_chunk(result.chunk)]
    )
    unique: list[SearchResult] = []
    seen: set[tuple[str, int, int]] = set()
    for result in filtered:
        source_range = (
            result.chunk.path,
            result.chunk.start_line,
            result.chunk.end_line,
        )
        if source_range in seen:
            continue
        seen.add(source_range)
        unique.append(result)

    diverse: list[SearchResult] = []
    overflow: list[SearchResult] = []
    per_file: dict[str, int] = {}
    for result in unique:
        path = result.chunk.path
        if per_file.get(path, 0) >= max_per_file:
            overflow.append(result)
            continue
        diverse.append(result)
        per_file[path] = per_file.get(path, 0) + 1

    if len(diverse) < min_limit:
        diverse.extend(overflow[: min_limit - len(diverse)])

    selected: list[SearchResult] = []
    for candidate in diverse:
        if len(selected) >= max_limit:
            break
        if len(selected) >= min_limit and not context_candidate_supported(
            selected, candidate
        ):
            break
        selected.append(candidate)
    return selected


def planning_retrieval_limit(max_chunks: int) -> int:
    """Return a small result pool that still leaves room for filtering/diversity."""
    if max_chunks < 1:
        raise ValueError("max_chunks must be at least 1")
    return max(max_chunks, min(20, max(12, max_chunks * 2)))


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
        f"Query: {query} · Repository: {repository}",
    ]
    if not results:
        lines.append("No relevant context found.")
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
                f"{fence}{language}",
                chunk.code,
                fence,
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
