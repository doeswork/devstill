from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from devstill.config import load_config


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(
        self, texts: list[str], show_progress: bool = False
    ) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [
                float("authentication" in text.casefold()),
                float("profile" in text.casefold()),
                1.0,
            ]
            for text in texts
        ]


class FakeChroma:
    def __init__(self) -> None:
        self.records: dict[str, tuple[list[float], str]] = {}

    def reset_collection(self) -> None:
        self.records.clear()

    def upsert_search_units(
        self,
        units: list[Any],
        embeddings: list[list[float]],
        chunks: dict[str, Any],
    ) -> None:
        for unit, vector in zip(units, embeddings, strict=True):
            self.records[unit.id] = (vector, unit.source_chunk_ids[0])

    def delete_search_units(self, unit_ids: list[str]) -> None:
        for unit_id in unit_ids:
            self.records.pop(unit_id, None)

    def query_embedding(
        self, embedding: list[float], limit: int
    ) -> list[tuple[str, float]]:
        values = []
        for vector, chunk_id in self.records.values():
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
