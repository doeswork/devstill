from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_LOCKFILES = {
    "bun.lock",
    "bun.lockb",
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "package-lock.json",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}
_NOISY_DIRS = {"vendor", "vendors", "third_party", "generated"}
_GENERATED_BASENAMES = {"gradlew", "gradlew.bat"}
_GENERATED_RE = re.compile(
    r"(?:generated|auto-?generated).{0,40}(?:do not edit|don't edit)|"
    r"(?:do not edit|don't edit).{0,40}(?:generated|auto-?generated)",
    re.IGNORECASE | re.DOTALL,
)


def is_binary(raw: bytes) -> bool:
    sample = raw[:8192]
    if b"\0" in sample:
        return True
    if not sample:
        return False
    control = sum(value < 9 or (13 < value < 32) or value == 127 for value in sample)
    return control / len(sample) > 0.08


def path_is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return is_binary(handle.read(8192))


def noisy_reason(path: Path, relative: str, raw: bytes) -> str | None:
    name = path.name.casefold()
    parts = {part.casefold() for part in PurePosixPath(relative).parts[:-1]}
    if name in _GENERATED_BASENAMES:
        return "generated Gradle wrapper"
    if name in _LOCKFILES:
        return "lockfile"
    if path.suffix.casefold() == ".map":
        return "source map"
    if name.endswith((".min.js", ".min.css")):
        return "minified bundle"
    matched = parts & _NOISY_DIRS
    if matched:
        return f"vendored/generated directory ({min(matched)})"
    header = raw[:4096].decode("utf-8", errors="ignore")
    if _GENERATED_RE.search(header):
        return "generated-do-not-edit header"
    if len(raw) > 8000 and raw.count(b"\n") <= 1:
        return "probable minified/generated single-line file"
    return None
