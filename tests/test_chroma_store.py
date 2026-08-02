from __future__ import annotations

from dataclasses import replace

from devstill.context import build_search_unit
from devstill.storage.chroma_store import ChromaStore
from tests.test_sqlite_store import sample_chunk


def test_chroma_persists_queries_and_deletes(tmp_path) -> None:
    path = tmp_path / "chroma"
    chunk = sample_chunk()
    unit = build_search_unit(chunk)
    store = ChromaStore(path, "test_collection")
    store.upsert_search_units([unit], [[1.0, 0.0]], {chunk.id: chunk})
    assert store.query_embedding([1.0, 0.0], 1)[0][0] == chunk.id
    reopened = ChromaStore(path, "test_collection")
    assert reopened.query_embedding([1.0, 0.0], 1)[0][0] == chunk.id
    reopened.delete_search_units([unit.id])
    assert reopened.query_embedding([1.0, 0.0], 1) == []


def test_chroma_batches_upserts_and_deletes_at_runtime_limit() -> None:
    class FakeClient:
        def get_max_batch_size(self):
            return 3

    class FakeCollection:
        def __init__(self):
            self.upserts = []
            self.deletes = []

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

        def delete(self, **kwargs):
            self.deletes.append(kwargs)

    store = ChromaStore.__new__(ChromaStore)
    store.client = FakeClient()
    store.collection = FakeCollection()
    base = sample_chunk()
    chunks = [
        replace(
            base,
            id=f"chunk-{index}",
            path=f"src/file_{index}.py",
            filename=f"file_{index}.py",
            code=f"value = {index}",
        )
        for index in range(7)
    ]
    units = [build_search_unit(chunk) for chunk in chunks]
    vectors = [[float(index), 1.0] for index in range(7)]
    chunk_map = {chunk.id: chunk for chunk in chunks}
    upsert_progress = []

    store.upsert_search_units(
        units,
        vectors,
        chunk_map,
        progress=lambda current, total: upsert_progress.append((current, total)),
    )

    assert [len(batch["ids"]) for batch in store.collection.upserts] == [3, 3, 1]
    assert upsert_progress == [(3, 7), (6, 7), (7, 7)]
    flattened_ids = [
        value for batch in store.collection.upserts for value in batch["ids"]
    ]
    flattened_vectors = [
        value for batch in store.collection.upserts for value in batch["embeddings"]
    ]
    flattened_documents = [
        value for batch in store.collection.upserts for value in batch["documents"]
    ]
    flattened_chunk_ids = [
        metadata["chunk_id"]
        for batch in store.collection.upserts
        for metadata in batch["metadatas"]
    ]
    assert flattened_ids == [unit.id for unit in units]
    assert flattened_vectors == vectors
    assert flattened_documents == [chunk.code for chunk in chunks]
    assert flattened_chunk_ids == [chunk.id for chunk in chunks]

    delete_progress = []
    store.delete_search_units(
        [unit.id for unit in units],
        progress=lambda current, total: delete_progress.append((current, total)),
    )
    assert [len(batch["ids"]) for batch in store.collection.deletes] == [3, 3, 1]
    assert delete_progress == [(3, 7), (6, 7), (7, 7)]
