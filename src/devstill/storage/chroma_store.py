from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from devstill.models import SearchUnit, SourceChunk


class ChromaStore:
    def __init__(self, path: Path, collection_name: str) -> None:
        import chromadb

        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path))
        self.collection_name = collection_name
        self.collection = self.client.get_or_create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def reset_collection(self) -> None:
        if self.collection_name in {
            item.name for item in self.client.list_collections()
        }:
            self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert_search_units(
        self,
        units: Sequence[SearchUnit],
        embeddings: Sequence[Sequence[float]],
        chunks: dict[str, SourceChunk],
    ) -> None:
        if not units:
            return
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for unit in units:
            chunk = chunks[unit.source_chunk_ids[0]]
            documents.append(chunk.code)
            metadatas.append(
                {
                    "path": chunk.path,
                    "filename": chunk.filename,
                    "language": chunk.language,
                    "kind": chunk.kind,
                    "name": chunk.name or "",
                    "qualname": chunk.qualname or "",
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "content_hash": chunk.content_hash,
                    "chunk_id": chunk.id,
                }
            )
        self.collection.upsert(
            ids=[unit.id for unit in units],
            embeddings=[list(vector) for vector in embeddings],
            documents=documents,
            metadatas=metadatas,
        )

    def delete_search_units(self, unit_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(unit_ids))
        if ids:
            self.collection.delete(ids=ids)

    def query_embedding(
        self, embedding: Sequence[float], limit: int
    ) -> list[tuple[str, float]]:
        if self.collection.count() == 0:
            return []
        result = self.collection.query(
            query_embeddings=[list(embedding)],
            n_results=min(limit, self.collection.count()),
            include=["distances", "metadatas"],
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        values: list[tuple[str, float]] = []
        for unit_id, distance, metadata in zip(ids, distances, metadatas, strict=False):
            chunk_id = (metadata or {}).get("chunk_id", unit_id)
            values.append((str(chunk_id), 1.0 - float(distance)))
        return values
