from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from devstill.hashing import hash_text
from devstill.models import ParseError, SourceChunk
from devstill.parsers.base import ParseResult
from devstill.paths import relative_repo_path

_TEMPLATE_TOKEN = re.compile(r"{%.*?%}|{{.*?}}|{#.*?#}", re.DOTALL)
_TEMPLATE_STATEMENT = re.compile(
    r"^{%-?\s*(block|macro|endblock|endmacro)\b(?:\s+([A-Za-z_][\w.-]*))?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _TemplateRegion:
    start_line: int
    end_line: int
    kind: str
    name: str


def has_django_jinja_syntax(source: str) -> bool:
    return _TEMPLATE_TOKEN.search(source) is not None


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _template_regions(source: str, end_line: int) -> list[_TemplateRegion]:
    stack: list[tuple[str, str, int]] = []
    regions: list[_TemplateRegion] = []
    for token in _TEMPLATE_TOKEN.finditer(source):
        if not token.group(0).startswith("{%"):
            continue
        statement = _TEMPLATE_STATEMENT.match(token.group(0))
        if statement is None:
            continue
        kind = statement.group(1).casefold()
        name = statement.group(2)
        line = _line_number(source, token.start())
        if kind in {"block", "macro"} and name:
            stack.append((kind, name, line))
            continue
        opening = kind.removeprefix("end")
        if not stack or stack[-1][0] != opening:
            continue
        region_kind, region_name, start = stack.pop()
        if not stack:
            regions.append(_TemplateRegion(start, line, region_kind, region_name))
    if stack:
        outer_kind, outer_name, start = stack[0]
        regions.append(_TemplateRegion(start, end_line, outer_kind, outer_name))
    return sorted(regions, key=lambda region: (region.start_line, region.end_line))


def _template_sections(
    source: str, end_line: int
) -> list[tuple[int, int, str, str | None, str | None, str]]:
    regions = _template_regions(source, end_line)
    if not regions:
        return [(1, end_line, "template_document", None, None, "document")]
    sections: list[tuple[int, int, str, str | None, str | None, str]] = []
    cursor = 1
    for region in regions:
        if region.start_line < cursor:
            return [(1, end_line, "template_document", None, None, "document")]
        if cursor < region.start_line:
            name = "preamble" if cursor == 1 else f"section@{cursor}"
            sections.append(
                (
                    cursor,
                    region.start_line - 1,
                    "template_section",
                    name,
                    None,
                    "section",
                )
            )
        sections.append(
            (
                region.start_line,
                region.end_line,
                "template_block",
                region.name,
                region.name,
                region.kind,
            )
        )
        cursor = region.end_line + 1
    if cursor <= end_line:
        sections.append(
            (
                cursor,
                end_line,
                "template_section",
                f"section@{cursor}",
                None,
                "section",
            )
        )
    return sections


def parse_template_text(path: Path, repo_root: Path) -> ParseResult:
    """Create exact Django/Jinja-aware fallback chunks without parsing templates."""
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
    lines = source.splitlines(keepends=True)
    end_line = max(1, len(source.splitlines()))
    parent_id = f"{relative}::document"

    def code(start: int, end: int) -> str:
        return "".join(lines[start - 1 : end])

    parent = SourceChunk(
        id=parent_id,
        path=relative,
        filename=path.name,
        language="html",
        kind="document",
        name=path.name,
        qualname=None,
        parent_id=None,
        start_line=1,
        end_line=end_line,
        code=source,
        leading_comments=None,
        content_hash=hash_text(source),
        parser_metadata={
            "parser": "template_text",
            "searchable": False,
            "template_aware": True,
        },
    )
    chunks = [parent]
    used_ids: dict[str, int] = {}
    for start, end, kind, name, qualname, template_kind in _template_sections(
        source, end_line
    ):
        chunk_code = code(start, end)
        if not chunk_code.strip():
            continue
        label = qualname or name or f"section@{start}"
        base = f"{relative}::{label}::{kind}"
        count = used_ids.get(base, 0) + 1
        used_ids[base] = count
        chunk_id = base if count == 1 else f"{base}::{count}"
        chunks.append(
            SourceChunk(
                id=chunk_id,
                path=relative,
                filename=path.name,
                language="html",
                kind=kind,
                name=name or path.name,
                qualname=qualname,
                parent_id=parent_id,
                start_line=start,
                end_line=end,
                code=chunk_code,
                leading_comments=None,
                content_hash=hash_text(chunk_code),
                parser_metadata={
                    "parser": "template_text",
                    "fallback": True,
                    "template_aware": True,
                    "template_kind": template_kind,
                },
            )
        )
    return ParseResult(chunks=chunks)


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
