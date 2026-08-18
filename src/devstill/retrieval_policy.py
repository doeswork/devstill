from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

_GLOB_CHARACTERS = frozenset("*?[]")


def normalize_retrieval_pattern(value: str) -> str:
    """Validate and normalize a repository-relative path or glob."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("retrieval exclusion must not be blank")
    if len(normalized) > 240:
        raise ValueError("retrieval exclusion must be at most 240 characters")
    if "\0" in normalized or "\\" in normalized:
        raise ValueError("retrieval exclusions must use repository-relative POSIX paths")
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

