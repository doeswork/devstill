from __future__ import annotations

from pathlib import Path

from devstill.config import DevstillConfig, FilesConfig
from devstill.content_policy import ContentDecision, classify_content
from devstill.models import ChunkPolicy, FileQuality
from devstill.parsers.base import ParseResult
from devstill.parsers.bounded import bounded_chunks, chunk_line_count
from devstill.parsers.plain_text import (
    has_django_jinja_syntax,
    parse_template_text,
)
from devstill.parsers.registry import FALLBACK_SPEC
from devstill.paths import relative_repo_path


def _quality(
    relative: str,
    parser: str,
    result: ParseResult,
    fallback_segments: int,
    *,
    structural: bool,
    category: str,
    disposition: str = "indexed",
    noisy: bool = False,
    reasons: list[str] | None = None,
    recovered_fallback: str | None = None,
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
    forced_windows = any(
        chunk.parser_metadata.get("fallback_window") for chunk in searchable
    )
    if disposition != "indexed":
        status = "SKIPPED"
    elif recovered_fallback:
        status = "MEH"
        if recovered_fallback == "template_text":
            all_reasons.append(
                "structural parser failed; exact template-aware fallback used"
            )
        else:
            all_reasons.append("structural parser failed; exact bounded fallback used")
    elif result.errors:
        status = "BAD"
    elif result.skipped or not result.chunks:
        status = "SKIPPED"
    elif category == "unknown_text":
        status = "MEH"
        all_reasons.append("generic text fallback")
    elif fallback_segments and (category == "code" or forced_windows):
        status = "MEH"
        all_reasons.append("oversized chunks required fallback segmentation")
    elif not searchable:
        status = "MEH"
        all_reasons.append("no useful searchable structural chunks")
    else:
        status = "GOOD"
    return FileQuality(
        path=relative,
        status=status,
        parser=parser,
        category=category,
        disposition=disposition,
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
    include_ignored: list[str],
    decision: ContentDecision | None = None,
) -> ParseResult:
    relative = relative_repo_path(repo_root, path)
    raw = path.read_bytes()
    if decision is None:
        files = FilesConfig(
            index_noisy_files=index_noisy_files,
            include_ignored=include_ignored,
        )
        decision = classify_content(
            path,
            relative,
            raw,
            DevstillConfig(files=files),
        )
    spec = decision.parser_spec
    if decision.disposition != "indexed":
        parser = "binary" if decision.category == "binary" else "content_policy"
        result = ParseResult(
            skipped=True,
            binary=decision.category == "binary",
            parser=parser,
        )
        result.quality = _quality(
            relative,
            parser,
            result,
            0,
            structural=spec.structural,
            category=decision.category,
            disposition=decision.disposition,
            noisy=decision.noisy,
            reasons=[decision.reason] if decision.reason else None,
        )
        return result

    active_spec = FALLBACK_SPEC if decision.noisy else spec
    result = active_spec.parse(path, repo_root)
    recovery_parser: str | None = None
    recovery_errors = False
    if active_spec.structural and result.errors and not result.skipped:
        primary_errors = list(result.errors)
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            source = ""
        if active_spec.language == "html" and has_django_jinja_syntax(source):
            fallback = parse_template_text(path, repo_root)
            recovery_parser = "template_text"
        else:
            fallback = FALLBACK_SPEC.parse(path, repo_root)
            recovery_parser = FALLBACK_SPEC.name
        recovery_errors = bool(fallback.errors) or fallback.skipped
        result = ParseResult(
            chunks=fallback.chunks,
            errors=[*primary_errors, *fallback.errors],
            skipped=fallback.skipped,
            binary=fallback.binary,
        )
        for chunk in result.chunks:
            chunk.parser_metadata.update(
                {
                    "fallback": chunk.parser_metadata.get("fallback", True),
                    "structural_fallback": active_spec.name,
                    "recovery_parser": recovery_parser,
                }
            )
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
    if decision.noisy:
        for chunk in result.chunks:
            chunk.parser_metadata["noisy"] = True
    searchable = [
        chunk
        for chunk in result.chunks
        if chunk.parser_metadata.get("searchable", True)
    ]
    recovered_fallback = (
        recovery_parser
        if recovery_parser
        and searchable
        and not recovery_errors
        and not boundary_errors
        else None
    )
    result.quality = _quality(
        relative,
        active_spec.name,
        result,
        fallback_segments,
        structural=active_spec.structural,
        category=decision.category,
        noisy=decision.noisy,
        reasons=[decision.reason] if decision.reason else None,
        recovered_fallback=recovered_fallback,
    )
    return result
