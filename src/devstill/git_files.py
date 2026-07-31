from __future__ import annotations

import fnmatch
import os
import subprocess
import warnings
from pathlib import Path, PurePosixPath

from devstill.config import DevstillConfig

ALWAYS_EXCLUDED_DIRS = {
    ".git",
    ".devstill",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
}


def _matches(path: str, patterns: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate.match(pattern)
        or fnmatch.fnmatchcase(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
        for pattern in patterns
    )


def _always_excluded(path: str) -> bool:
    return any(part in ALWAYS_EXCLUDED_DIRS for part in PurePosixPath(path).parts)


def _configured(path: str, config: DevstillConfig) -> bool:
    return (
        not _always_excluded(path)
        and _matches(path, config.files.include)
        and not _matches(path, config.files.exclude)
    )


def _git_files(repo_root: Path, use_gitignore: bool) -> list[str] | None:
    probe = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return None
    command = ["git", "-C", str(repo_root), "ls-files", "-co"]
    if use_gitignore:
        command.append("--exclude-standard")
    command.append("-z")
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def _fallback_files(repo_root: Path) -> list[str]:
    warnings.warn(
        "Repository is not a Git work tree; fallback scanning cannot fully reproduce Git ignore semantics.",
        stacklevel=2,
    )
    paths: list[str] = []
    for current, directories, filenames in os.walk(repo_root):
        directories[:] = sorted(d for d in directories if d not in ALWAYS_EXCLUDED_DIRS)
        base = Path(current)
        for filename in sorted(filenames):
            path = base / filename
            if path.is_file() and not path.is_symlink():
                paths.append(path.relative_to(repo_root).as_posix())
    return paths


def discover_repository_files(repo_root: Path, config: DevstillConfig) -> list[Path]:
    root = repo_root.expanduser().resolve()
    raw_paths = _git_files(root, config.files.use_gitignore)
    if raw_paths is None:
        raw_paths = _fallback_files(root)
    selected: list[Path] = []
    for relative in sorted(set(raw_paths)):
        if not _configured(relative, config):
            continue
        path = root / relative
        if path.is_file() and path.resolve().is_relative_to(root):
            selected.append(path)
    return selected
