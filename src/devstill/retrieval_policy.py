from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

from devstill.models import SourceChunk

_GLOB_CHARACTERS = frozenset("*?[]")


def query_references(query: str) -> list[str]:
    """Extract bounded explicit code references, without generating new symbols."""
    matches = re.finditer(
        r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b|\b[A-Za-z_]\w*_\w+\b|`([A-Za-z_]\w*)`",
        query,
    )
    return list(dict.fromkeys(m.group(1) or m.group(0) for m in matches))[:6]


def is_overload(chunk: SourceChunk) -> bool:
    return chunk.language == "python" and any(
        str(d).rsplit(".", 1)[-1] == "overload"
        for d in chunk.parser_metadata.get("decorators", [])
    )


def requests_signatures(query: str) -> bool:
    return bool(
        re.search(
            r"\b(overloads?|signatures?|typing|type hints?|type annotations?)\b",
            query,
            re.IGNORECASE,
        )
    )


def normalize_retrieval_pattern(value: str) -> str:
    """Validate and normalize a repository-relative path or glob."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("retrieval exclusion must not be blank")
    if len(normalized) > 240:
        raise ValueError("retrieval exclusion must be at most 240 characters")
    if "\0" in normalized or "\\" in normalized:
        raise ValueError(
            "retrieval exclusions must use repository-relative POSIX paths"
        )
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("retrieval exclusions must not use drive-letter paths")
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized.startswith("./") or ".." in path.parts:
        raise ValueError("retrieval exclusions must stay inside the repository")
    if normalized.endswith("/"):
        normalized = f"{normalized}**"
    if any(part in {"", "."} for part in PurePosixPath(normalized).parts):
        raise ValueError("retrieval exclusion contains an invalid path segment")
    return normalized


def is_exact_retrieval_pattern(pattern: str) -> bool:
    return not any(character in pattern for character in _GLOB_CHARACTERS)


def matches_retrieval_exclude(path: str, patterns: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate.match(pattern)
        or fnmatch.fnmatchcase(path, pattern)
        or (
            pattern.startswith("**/")
            and fnmatch.fnmatchcase(path, pattern.removeprefix("**/"))
        )
        for pattern in patterns
    )
