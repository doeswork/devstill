from __future__ import annotations

from pathlib import Path

from devstill.hashing import hash_text
from devstill.models import ParseError, SourceChunk
from devstill.parsers.base import ParseResult
from devstill.paths import relative_repo_path


def parse_plain_text(path: Path, repo_root: Path) -> ParseResult:
    relative = relative_repo_path(repo_root, path)
    raw = path.read_bytes()
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ParseResult(
            errors=[ParseError(relative, f"File is not valid UTF-8: {exc}")],
            skipped=True,
        )
    if not source:
        return ParseResult()
    end_line = max(1, len(source.splitlines()))
    parent_id = f"{relative}::document"
    parent = SourceChunk(
        id=parent_id,
        path=relative,
        filename=path.name,
        language=path.suffix.lower().lstrip(".") or "text",
        kind="document",
        name=path.name,
        qualname=None,
        parent_id=None,
        start_line=1,
        end_line=end_line,
        code=source,
        leading_comments=None,
        content_hash=hash_text(source),
        parser_metadata={"parser": "plain_text", "searchable": False},
    )
    segment = SourceChunk(
        id=f"{parent_id}::segment@1",
        path=relative,
        filename=path.name,
        language=parent.language,
        kind="document_segment",
        name=path.name,
        qualname=None,
        parent_id=parent_id,
        start_line=1,
        end_line=end_line,
        code=source,
        leading_comments=None,
        content_hash=hash_text(source),
        parser_metadata={"parser": "plain_text", "fallback": True},
    )
    return ParseResult(chunks=[parent, segment])
