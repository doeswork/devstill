from __future__ import annotations

from pathlib import Path

from devstill.models import ChunkPolicy, FileQuality
from devstill.parsers.base import ParseResult
from devstill.parsers.bounded import bounded_chunks, chunk_line_count
from devstill.parsers.detection import is_binary, noisy_reason
from devstill.parsers.registry import FALLBACK_SPEC, parser_spec_for
from devstill.paths import relative_repo_path


def _quality(
    relative: str,
    parser: str,
    result: ParseResult,
    fallback_segments: int,
    *,
    structural: bool,
    noisy: bool = False,
    reasons: list[str] | None = None,
) -> FileQuality:
    searchable = [
        chunk
        for chunk in result.chunks
        if chunk.parser_metadata.get("searchable", True)
    ]
    all_reasons = list(reasons or [])
    if result.errors:
        all_reasons.extend(error.message for error in result.errors)
    if not result.chunks and not result.errors and not result.skipped:
        all_reasons.append("empty file")
    if result.errors:
        status = "BAD"
    elif noisy or result.skipped or not result.chunks:
        status = "MEH"
    elif not structural:
        status = "MEH"
        all_reasons.append("generic text fallback")
    elif fallback_segments:
        status = "MEH"
        all_reasons.append("oversized structural chunks required segmentation")
    elif not searchable:
        status = "MEH"
        all_reasons.append("no useful searchable structural chunks")
    else:
        status = "GOOD"
    return FileQuality(
        path=relative,
        status=status,
        parser=parser,
        reasons=list(dict.fromkeys(all_reasons)),
        canonical_chunks=len(result.chunks),
        searchable_chunks=len(searchable),
        max_chunk_chars=max((len(chunk.code) for chunk in searchable), default=0),
        max_chunk_lines=max(
            (chunk_line_count(chunk) for chunk in searchable), default=0
        ),
        fallback_segments=fallback_segments,
        noisy=noisy,
        skipped=result.skipped,
    )


def parse_with_policy(
    path: Path,
    repo_root: Path,
    policy: ChunkPolicy,
    *,
    index_noisy_files: bool,
) -> ParseResult:
    relative = relative_repo_path(repo_root, path)
    raw = path.read_bytes()
    if is_binary(raw):
        return ParseResult(skipped=True, binary=True, parser="binary")

    spec = parser_spec_for(path)
    noisy = noisy_reason(path, relative, raw)
    if noisy and not index_noisy_files:
        result = ParseResult(skipped=True, parser=spec.name)
        result.quality = _quality(
            relative,
            spec.name,
            result,
            0,
            structural=spec.structural,
            noisy=True,
            reasons=[f"skipped noisy file: {noisy}"],
        )
        return result

    active_spec = FALLBACK_SPEC if noisy else spec
    result = active_spec.parse(path, repo_root)
    result.parser = active_spec.name
    chunks, boundary_errors, fallback_segments = bounded_chunks(result.chunks, policy)
    result.chunks = chunks
    result.errors.extend(boundary_errors)
    fallback_segments = max(
        fallback_segments,
        sum(
            bool(
                chunk.parser_metadata.get("fallback")
                or chunk.parser_metadata.get("segmented_from")
            )
            for chunk in result.chunks
            if chunk.parser_metadata.get("searchable", True)
        ),
    )
    if noisy:
        for chunk in result.chunks:
            chunk.parser_metadata["noisy"] = True
    result.quality = _quality(
        relative,
        active_spec.name,
        result,
        fallback_segments,
        structural=active_spec.structural,
        noisy=bool(noisy),
        reasons=[f"noisy file indexed by override: {noisy}"] if noisy else None,
    )
    return result
