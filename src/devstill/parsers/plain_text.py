from __future__ import annotations

from pathlib import Path

from devstill.hashing import hash_text
from devstill.models import ParseError, SourceChunk
from devstill.parsers.base import ParseResult
from devstill.paths import relative_repo_path


def parse_plain_text(path: Path, repo_root: Path) -> ParseResult:
    relative = relative_repo_path(repo_root, path)
    raw = path.read_bytes()
    if b"\0" in raw:
        return ParseResult(skipped=True)
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ParseResult(
            errors=[ParseError(relative, f"File is not valid UTF-8: {exc}")],
            skipped=True,
        )
    end_line = max(1, len(source.splitlines()))
    chunk = SourceChunk(
        id=f"{relative}::document",
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
        parser_metadata={"parser": "plain_text"},
    )
    return ParseResult(chunks=[chunk])
