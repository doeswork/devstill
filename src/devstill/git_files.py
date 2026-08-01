from __future__ import annotations

import os
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from devstill.config import DevstillConfig
from devstill.content_policy import ContentDecision, classify_content, matches_patterns

ALWAYS_EXCLUDED_DIRS = {
    ".git",
    ".devstill",
    ".venv",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
}


def _always_excluded(path: str) -> bool:
    return any(part in ALWAYS_EXCLUDED_DIRS for part in PurePosixPath(path).parts)


def _configured(path: str, config: DevstillConfig) -> bool:
    return (
        not _always_excluded(path)
        and (config.files.scan_all_text or matches_patterns(path, config.files.include))
        and not matches_patterns(path, config.files.exclude)
    )


@dataclass(frozen=True, slots=True)
class RepositoryFile:
    path: Path
    relative: str
    decision: ContentDecision


@dataclass(frozen=True, slots=True)
class RepositoryInventory:
    files: list[RepositoryFile]

    @property
    def eligible(self) -> list[RepositoryFile]:
        return [item for item in self.files if item.decision.disposition == "indexed"]

    @property
    def ignored(self) -> list[RepositoryFile]:
        return [item for item in self.files if item.decision.disposition == "ignored"]

    @property
    def skipped(self) -> list[RepositoryFile]:
        return [item for item in self.files if item.decision.disposition == "skipped"]


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


def discover_repository_inventory(
    repo_root: Path, config: DevstillConfig
) -> RepositoryInventory:
    root = repo_root.expanduser().resolve()
    raw_paths = _git_files(root, config.files.use_gitignore)
    if raw_paths is None:
        raw_paths = _fallback_files(root)
    selected: list[RepositoryFile] = []
    for relative in sorted(set(raw_paths)):
        if not _configured(relative, config):
            continue
        path = root / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root)
        ):
            continue
        raw = path.read_bytes()
        selected.append(
            RepositoryFile(
                path, relative, classify_content(path, relative, raw, config)
            )
        )
    return RepositoryInventory(selected)


def discover_repository_files(repo_root: Path, config: DevstillConfig) -> list[Path]:
    return [
        item.path for item in discover_repository_inventory(repo_root, config).eligible
    ]
