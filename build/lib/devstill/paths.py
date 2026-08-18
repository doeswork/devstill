from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class IndexPaths:
    root: Path
    sqlite: Path
    chroma: Path
    cache: Path
    logs: Path
    manifests: Path
    dirty_marker: Path

    @classmethod
    def from_chroma_path(cls, chroma_path: Path) -> IndexPaths:
        root = chroma_path.parent
        return cls(
            root=root,
            sqlite=root / "metadata.sqlite",
            chroma=chroma_path,
            cache=root / "cache",
            logs=root / "logs",
            manifests=root / "manifests",
            dirty_marker=root / ".indexing-dirty",
        )

    def create(self) -> None:
        for path in (self.root, self.chroma, self.cache, self.logs, self.manifests):
            path.mkdir(parents=True, exist_ok=True)


def relative_repo_path(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()
