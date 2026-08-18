from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

from devstill.hashing import hash_text
from devstill.models import ChunkPolicy, ParseError, SourceChunk


def chunk_line_count(chunk: SourceChunk) -> int:
    return max(1, chunk.end_line - chunk.start_line + 1)


def within_policy(chunk: SourceChunk, policy: ChunkPolicy) -> bool:
    return (
        len(chunk.code) <= policy.max_chars
        and chunk_line_count(chunk) <= policy.max_lines
    )


def _groups(
    source: str,
    start_line: int,
    split_points: list[int] | None,
) -> list[str]:
    lines = source.splitlines(keepends=True)
    if not lines:
        return []
    if split_points:
        boundaries = [0]
        boundaries.extend(
            point - start_line
            for point in split_points
            if 0 < point - start_line < len(lines)
        )
        boundaries.append(len(lines))
        boundaries = sorted(set(boundaries))
        return [
            "".join(lines[left:right])
            for left, right in pairwise(boundaries)
            if left < right
        ]
    groups: list[str] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        stripped = line.strip()
        if not stripped or stripped.startswith(("# ", "## ", "### ", "---", "===")):
            groups.append("".join(current))
            current = []
    if current:
        groups.append("".join(current))
    return groups


def _force_windows(
    text: str,
    start_line: int,
    policy: ChunkPolicy,
) -> tuple[list[tuple[int, int, str]], bool]:
    lines = text.splitlines(keepends=True)
    if not lines and text == "":
        return [], False
    if any(len(line) > policy.max_chars for line in lines):
        return [], True
    output: list[tuple[int, int, str]] = []
    cursor = 0
    overlap = min(policy.overlap_lines, max(0, policy.max_lines - 1))
    while cursor < len(lines):
        end = cursor
        chars = 0
        while end < len(lines) and end - cursor < policy.max_lines:
            next_chars = chars + len(lines[end])
            if next_chars > policy.max_chars and end > cursor:
                break
            chars = next_chars
            end += 1
        if end == cursor:
            return output, True
        code = "".join(lines[cursor:end])
        output.append((start_line + cursor, start_line + end - 1, code))
        if end >= len(lines):
            break
        cursor = max(cursor + 1, end - overlap)
    return output, False


def split_bounded(
    source: str,
    start_line: int,
    policy: ChunkPolicy,
    *,
    natural: bool,
    split_points: list[int] | None = None,
) -> tuple[list[tuple[int, int, str]], bool, bool]:
    line_count = max(1, len(source.splitlines()))
    if len(source) <= policy.max_chars and line_count <= policy.max_lines:
        return [(start_line, start_line + line_count - 1, source)], False, False
    if not natural:
        values, rejected = _force_windows(source, start_line, policy)
        return values, rejected, True

    groups = _groups(source, start_line, split_points)
    packed: list[tuple[int, int, str]] = []
    current = ""
    current_start = start_line
    line_cursor = start_line
    forced = False
    for group in groups:
        group_lines = max(1, len(group.splitlines()))
        if len(group) > policy.max_chars or group_lines > policy.max_lines:
            if current:
                lines = max(1, len(current.splitlines()))
                packed.append((current_start, current_start + lines - 1, current))
                current = ""
            windows, rejected = _force_windows(group, line_cursor, policy)
            if rejected:
                return packed, True, True
            packed.extend(windows)
            forced = True
            line_cursor += group_lines
            current_start = line_cursor
            continue
        combined = current + group
        combined_lines = max(1, len(combined.splitlines()))
        if current and (
            len(combined) > policy.max_chars or combined_lines > policy.max_lines
        ):
            lines = max(1, len(current.splitlines()))
            packed.append((current_start, current_start + lines - 1, current))
            current_start = line_cursor
            current = group
        else:
            current += group
        line_cursor += group_lines
    if current:
        lines = max(1, len(current.splitlines()))
        packed.append((current_start, current_start + lines - 1, current))
    return packed, False, forced


def bounded_chunks(
    chunks: list[SourceChunk],
    policy: ChunkPolicy,
) -> tuple[list[SourceChunk], list[ParseError], int]:
    output: list[SourceChunk] = []
    errors: list[ParseError] = []
    fallback_segments = 0
    used_ids = {chunk.id for chunk in chunks}
    for chunk in chunks:
        searchable = chunk.parser_metadata.get("searchable", True)
        if not searchable or within_policy(chunk, policy):
            output.append(chunk)
            continue
        is_fallback = bool(chunk.parser_metadata.get("fallback"))
        if not is_fallback:
            parent = replace(
                chunk,
                parser_metadata={
                    **chunk.parser_metadata,
                    "searchable": False,
                    "oversized_parent": True,
                },
            )
            output.append(parent)
        segments, rejected, forced = split_bounded(
            chunk.code,
            chunk.start_line,
            policy,
            natural=True,
            split_points=[
                int(value)
                for value in chunk.parser_metadata.get("split_points", [])
                if isinstance(value, int)
            ],
        )
        if rejected:
            errors.append(
                ParseError(
                    chunk.path,
                    (
                        f"Oversized {chunk.kind} {chunk.qualname or chunk.name or ''} "
                        "contains a single line that cannot be segmented safely"
                    ).strip(),
                    chunk.start_line,
                )
            )
            continue
        for ordinal, (start, end, code) in enumerate(segments, start=1):
            owner_id = chunk.parent_id if is_fallback else chunk.id
            base = f"{owner_id or chunk.id}::segment@{start}"
            segment_id = base
            collision = 1
            while segment_id in used_ids:
                collision += 1
                segment_id = f"{base}::{collision}"
            used_ids.add(segment_id)
            output.append(
                replace(
                    chunk,
                    id=segment_id,
                    parent_id=owner_id,
                    start_line=start,
                    end_line=end,
                    code=code,
                    content_hash=hash_text(code),
                    parser_metadata={
                        **chunk.parser_metadata,
                        "searchable": True,
                        "segment": ordinal,
                        "segmented_from": owner_id or chunk.id,
                        "fallback_window": forced,
                    },
                )
            )
            fallback_segments += 1
    return output, errors, fallback_segments
