from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
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

    @property
    def max_batch_size(self) -> int:
        value = int(self.client.get_max_batch_size())
        if value < 1:
            raise RuntimeError("Chroma returned an invalid maximum batch size")
        return value

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
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        if not units:
            return
        if len(units) != len(embeddings):
            raise ValueError("search units and embeddings must have the same length")
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
        ids = [unit.id for unit in units]
        vectors = [list(vector) for vector in embeddings]
        total = len(ids)
        for start in range(0, total, self.max_batch_size):
            end = min(start + self.max_batch_size, total)
            self.collection.upsert(
                ids=ids[start:end],
                embeddings=vectors[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )
            if progress is not None:
                progress(end, total)

    def delete_search_units(
        self,
        unit_ids: Iterable[str],
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        ids = list(dict.fromkeys(unit_ids))
        total = len(ids)
        for start in range(0, total, self.max_batch_size):
            end = min(start + self.max_batch_size, total)
            self.collection.delete(ids=ids[start:end])
            if progress is not None:
                progress(end, total)

    def query_embedding(
        self, embedding: Sequence[float], limit: int, *, where: dict | None = None
    ) -> list[tuple[str, float]]:
        return self.query_embeddings([embedding], limit, where=where)[0]

    def query_embeddings(
        self,
        embeddings: Sequence[Sequence[float]],
        limit: int,
        *,
        where: dict | None = None,
    ) -> list[list[tuple[str, float]]]:
        """Query several vectors in one Chroma call."""
        if not embeddings:
            return []
        count = self.collection.count()
        if count == 0:
            return [[] for _ in embeddings]
        result = self.collection.query(
            query_embeddings=[list(embedding) for embedding in embeddings],
            n_results=min(limit, count),
            include=["distances", "metadatas"],
            **({"where": where} if where else {}),
        )
        id_groups = result.get("ids", [[] for _ in embeddings])
        distance_groups = result.get("distances", [[] for _ in embeddings])
        metadata_groups = result.get("metadatas", [[] for _ in embeddings])
        groups: list[list[tuple[str, float]]] = []
        for ids, distances, metadatas in zip(
            id_groups,
            distance_groups,
            metadata_groups,
            strict=False,
        ):
            values: list[tuple[str, float]] = []
            for unit_id, distance, metadata in zip(
                ids, distances, metadatas, strict=False
            ):
                chunk_id = (metadata or {}).get("chunk_id", unit_id)
                values.append((str(chunk_id), 1.0 - float(distance)))
            groups.append(values)
        while len(groups) < len(embeddings):
            groups.append([])
        return groups
