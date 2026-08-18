from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from devstill.config import DevstillConfig, resolve_repo_root
from devstill.paths import IndexPaths
from devstill.repository import (
    has_managed_gitignore_block,
    remove_managed_gitignore_block,
)


@dataclass(frozen=True, slots=True)
class RemovalPlan:
    config_path: Path
    repo_root: Path
    index_root: Path
    index_targets: tuple[Path, ...]
    gitignore_path: Path
    index_only: bool
    default_layout: bool
    managed_gitignore: bool


@dataclass(slots=True)
class RemovalResult:
    removed: list[Path] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)
    preserved: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    gitignore_updated: bool = False
    config_removed: bool = False

    @property
    def succeeded(self) -> bool:
        return not self.errors


def _absolute_without_resolving(base: Path, value: str) -> Path:
    candidate = Path(os.path.expandvars(value)).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return Path(os.path.abspath(candidate))


def _is_ancestor_or_same(candidate: Path, path: Path) -> bool:
    return path == candidate or path.is_relative_to(candidate)


def _validate_removal_root(path: Path, repo_root: Path, config_dir: Path) -> None:
    path = Path(os.path.abspath(path))
    repo_root = Path(os.path.abspath(repo_root))
    config_dir = Path(os.path.abspath(config_dir))
    home = Path(os.path.abspath(Path.home()))
    filesystem_root = Path(path.anchor)
    if path in {filesystem_root, home, repo_root, config_dir}:
        raise ValueError(f"Refusing to remove dangerously broad path: {path}")
    if _is_ancestor_or_same(path, home) or _is_ancestor_or_same(path, repo_root):
        raise ValueError(f"Refusing to remove path containing protected data: {path}")


def _symlink_parent(path: Path) -> Path | None:
    for parent in path.parents:
        if parent == Path(path.anchor):
            break
        if parent.is_symlink():
            return parent
    return None


def build_removal_plan(
    config_path: Path,
    config: DevstillConfig,
    *,
    index_only: bool,
) -> RemovalPlan:
    config_path = Path(os.path.abspath(config_path.expanduser()))
    config_dir = config_path.parent
    repo_root = resolve_repo_root(config_path, config)
    chroma_path = _absolute_without_resolving(config_dir, config.index.path)
    index_paths = IndexPaths.from_chroma_path(chroma_path)
    default_root = _absolute_without_resolving(config_dir, ".devstill")
    default_layout = (
        index_paths.root == default_root
        and index_paths.chroma == default_root / "chroma"
    )

    if default_layout:
        _validate_removal_root(index_paths.root, repo_root, config_dir)
        targets = (index_paths.root,)
    else:
        _validate_removal_root(index_paths.root, repo_root, config_dir)
        linked_parent = _symlink_parent(index_paths.chroma)
        if linked_parent is not None:
            raise ValueError(
                f"Refusing to follow symlinked custom index parent: {linked_parent}"
            )
        targets = tuple(
            dict.fromkeys(
                (
                    index_paths.chroma,
                    index_paths.cache,
                    index_paths.logs,
                    index_paths.manifests,
                    index_paths.sqlite,
                    index_paths.sqlite.with_name(index_paths.sqlite.name + "-wal"),
                    index_paths.sqlite.with_name(index_paths.sqlite.name + "-shm"),
                    index_paths.root / ".context.md",
                    index_paths.root / ".context.json",
                    index_paths.root / ".plan.md",
                    index_paths.root / ".plan.json",
                    index_paths.root / ".deep-plan-initial.md",
                    index_paths.root / ".deep-plan-initial.json",
                    index_paths.root / ".deep-plan.md",
                    index_paths.root / ".deep-plan.json",
                    index_paths.root / ".execute.patch",
                    index_paths.root / ".execute.json",
                    index_paths.dirty_marker,
                )
            )
        )

    gitignore_path = config_dir / ".gitignore"
    return RemovalPlan(
        config_path=config_path,
        repo_root=repo_root,
        index_root=index_paths.root,
        index_targets=targets,
        gitignore_path=gitignore_path,
        index_only=index_only,
        default_layout=default_layout,
        managed_gitignore=has_managed_gitignore_block(gitignore_path),
    )


def _remove_path(path: Path) -> bool:
    if path.is_symlink():
        path.unlink()
        return True
    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def _remove_index(plan: RemovalPlan, result: RemovalResult) -> None:
    for path in plan.index_targets:
        try:
            if _remove_path(path):
                result.removed.append(path)
            else:
                result.missing.append(path)
        except OSError as exc:
            result.errors.append((path, str(exc)))
    if result.errors or plan.default_layout:
        return
    try:
        plan.index_root.rmdir()
        result.removed.append(plan.index_root)
    except FileNotFoundError:
        pass
    except OSError:
        if plan.index_root.exists() or plan.index_root.is_symlink():
            result.preserved.append(plan.index_root)


def execute_removal(plan: RemovalPlan) -> RemovalResult:
    result = RemovalResult()
    _remove_index(plan, result)
    if result.errors or plan.index_only:
        return result

    if plan.gitignore_path.is_symlink():
        result.errors.append(
            (
                plan.gitignore_path,
                "refusing to modify a symlinked .gitignore",
            )
        )
        return result
    try:
        result.gitignore_updated = remove_managed_gitignore_block(plan.gitignore_path)
    except OSError as exc:
        result.errors.append((plan.gitignore_path, str(exc)))
        return result

    try:
        if _remove_path(plan.config_path):
            result.removed.append(plan.config_path)
            result.config_removed = True
        else:
            result.missing.append(plan.config_path)
    except OSError as exc:
        result.errors.append((plan.config_path, str(exc)))
    return result
