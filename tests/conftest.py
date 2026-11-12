from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from devstill.config import load_config


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path: Path, monkeypatch):
    path = Path.cwd() / ".pytest_cache" / "credentials" / f"{tmp_path.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    monkeypatch.setenv(
        "DEVSTILL_CREDENTIALS_PATH",
        str(path),
    )
    yield
    path.unlink(missing_ok=True)


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(
        self,
        texts: list[str],
        show_progress: bool = False,
        progress=None,
    ) -> list[list[float]]:
        self.calls.append(list(texts))
        values = [
            [
                float("authentication" in text.casefold()),
                float("profile" in text.casefold()),
                1.0,
            ]
            for text in texts
        ]
        if progress is not None and texts:
            progress(len(texts), len(texts))
        return values


class FakeChroma:
    def __init__(self) -> None:
        self.records: dict[str, tuple[list[float], str]] = {}
        self.metadata: dict[str, dict] = {}

    def reset_collection(self) -> None:
        self.records.clear()
        self.metadata.clear()

    def upsert_search_units(
        self,
        units: list[Any],
        embeddings: list[list[float]],
        chunks: dict[str, Any],
        progress=None,
    ) -> None:
        for unit, vector in zip(units, embeddings, strict=True):
            self.records[unit.id] = (vector, unit.source_chunk_ids[0])
            chunk = chunks[unit.source_chunk_ids[0]]
            self.metadata[unit.id] = {"path": chunk.path, "kind": chunk.kind}
        if progress is not None and units:
            progress(len(units), len(units))

    def delete_search_units(self, unit_ids: list[str], progress=None) -> None:
        for unit_id in unit_ids:
            self.records.pop(unit_id, None)
        if progress is not None and unit_ids:
            progress(len(unit_ids), len(unit_ids))

    def query_embedding(
        self, embedding: list[float], limit: int, *, where: dict | None = None
    ) -> list[tuple[str, float]]:
        values = []

        def matches(metadata, expression):
            if not expression:
                return True
            if "$and" in expression:
                return all(matches(metadata, item) for item in expression["$and"])
            return all(
                metadata.get(key) in value["$in"]
                if isinstance(value, dict)
                else metadata.get(key) == value
                for key, value in expression.items()
            )

        for unit_id, (vector, chunk_id) in self.records.items():
            if not matches(self.metadata[unit_id], where):
                continue
            score = sum(
                left * right for left, right in zip(vector, embedding, strict=True)
            )
            values.append((chunk_id, score))
        return sorted(values, key=lambda value: -value[1])[:limit]


@pytest.fixture
def configured_repo(tmp_path: Path) -> tuple[Path, Path, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "devstill.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "repo": {"root": "."},
                "index": {"path": ".devstill/chroma", "collection": "tests"},
                "embedding": {
                    "model": str(tmp_path / "model"),
                    "device": "cpu",
                    "max_sequence_length": 2048,
                    "batch_size": 8,
                    "normalize_embeddings": True,
                },
                "files": {
                    "use_gitignore": True,
                    "scan_all_text": False,
                    "index_noisy_files": False,
                    "include": ["**/*.py", "**/*.tsx", "**/*.md"],
                    "exclude": [".devstill/**", ".venv/**"],
                },
                "llm": {
                    "enabled": False,
                    "base_url": "http://127.0.0.1:8080/v1",
                    "model": "",
                },
            }
        ),
        encoding="utf-8",
    )
    return repo, config_path, load_config(config_path)


# Shared by the structured-editor runtime tests (minimal runtime, repair,
# requirements); defined here so every pytest version can find it.
@pytest.fixture
def session(tmp_path, monkeypatch):
    from devstill.config import DevstillConfig
    from devstill.runtime.service import RuntimeSession

    (tmp_path / "a.py").write_text("x = 1\n")
    config = DevstillConfig()
    config.llm.enabled = True
    config.llm.model = "test"
    config.repo.root = str(tmp_path)
    config.index.path = str(tmp_path / ".devstill/chroma")
    # These tests exercise the structured two-response editor explicitly.
    config.editing.engine = "structured"
    runtime = RuntimeSession(tmp_path / "devstill.json", config)
    monkeypatch.setattr(
        runtime,
        "evidence",
        lambda query: [{"path": "a.py", "code": "x = 1", "lines": [1, 1]}],
    )
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a: json.dumps(
            {
                "edits": [{"path": "a.py", "old": "x = 1", "new": "x = 2"}],
                "reads": [],
                "checks": [["python", "-c", "import a; assert a.x == 2"]],
            }
        ),
    )
    yield runtime
    runtime.close()
